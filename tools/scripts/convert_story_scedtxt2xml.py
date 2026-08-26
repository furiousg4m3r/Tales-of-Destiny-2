"""
Convert already-translated "paired text" .sced dumps (Japanese line(s) prefixed
with '#', followed by the matching English line(s), blocks separated by a line
of dashes) into XML that TranslationApp.exe / TranslationLib.dll can load.

This replaces extract_sced_xml.py's XML writer. The big differences from the
old script, and why:

  1. Speakers are now linked correctly. TranslationApp's XMLEntry only has a
     <SpeakerId> (comma-separated ints) that points at the Id of an entry in
     the <Speakers> block -- there is no <Speaker> text element in its schema.
     The old script wrote a raw <Speaker>text</Speaker>, which TranslationApp
     simply ignores (it isn't one of the elements TranslationLib looks for),
     so speaker attribution was silently getting lost. This version tracks a
     Speaker -> Id table and writes <SpeakerId>N</SpeakerId> on each entry.

  2. <FriendlyName> is now the in-game location the file's dialogue takes
     place in, instead of the filename. That location line is simply the
     first block in the .txt file (e.g. "Cresta Forest", "Cresta Inn &
     Grocery") -- confirmed against all three sample files. We take its
     English half as FriendlyName. We do NOT drop it from the Strings
     output, since these location banners still appear on-screen and may
     need retranslating/proofing like anything else.

  3. Entries are read straight from the pre-translated txt pairs (Japanese +
     English already given) rather than decoded from a raw .sced binary, so
     Status defaults to "Editing" (drafted, not yet proofread) when an
     English line is present, and "To Do" when it's still blank.

Block classification rules (each block = lines between '-----------------------'
separators):
  - Any line in the block starting with '#' -> those are the Japanese lines
    (with '#' stripped); every other line in the block is the English
    translation. This works regardless of how many lines are on each side.
  - A block with no '#' lines at all:
      - if it's a single line matching '<...>' (e.g. <Kyle>, <char:000003E8>)
        -> a speaker marker for whatever entry comes next.
      - if it's a single line matching a known system/code keyword
        (notice, select, menu, or something call-shaped like
        "set_parameter( ... );") -> its own "system" entry, not translated.
      - otherwise -> raw, not-yet-translated Japanese text (English blank).

Speaker pairing: mirrors the heuristic from extract_sced_xml.py's
is_npc_speaker (a short Japanese+English pair with no dialogue punctuation,
immediately followed by real dialogue, is treated as a speaker name) but adds
one guard: if the very next block is an exact duplicate of the candidate
(e.g. a location banner printed twice in a row, as in 06307), it is NOT
treated as a speaker -- a real speaker name is never immediately followed by
itself.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

SEPARATOR = '-----------------------'

MAIN_CHAR_NAMES = {
    '<Kyle>': 'Kyle', '<Reala>': 'Reala', '<Loni>': 'Loni',
    '<Judas>': 'Judas', '<Nanaly>': 'Nanaly', '<Harold>': 'Harold',
}

SYSTEM_KEYWORDS = [
    'notice', 'NOTICE', 'select', 'menu', 'set_parameter', 'wait_map',
    'exit-party', 'wait_', 'exit_', 'move_', 'call_', 'jump_',
]

TAG_RE = re.compile(r'^<[^<>]+>$')
DIALOGUE_END_PUNCT = ('！', '？', '。', '、', '…', '･･', '♪', '!')
DIALOGUE_CHARS = ('！', '？', '。', '…', '♪', '～', '、')
# Common verb/sentence-final endings: a real speaker name (a proper noun or
# title like "シナモン"/"食材屋") never ends in these, but short complete
# sentences like "いってらっしゃいませ" (Have a good day) do, and would
# otherwise slip past the length/punctuation check above.
SENTENCE_END_SUFFIXES = ('ませ', 'ます', 'ません', 'でした', 'です', 'ください', 'なさい')


# ---------------------------------------------------------------- parsing --

def parse_blocks(filepath):
    with open(filepath, encoding='utf-8') as f:
        raw = f.read()
    lines = raw.split('\n')
    blocks, current = [], []
    for line in lines:
        if line.strip() == SEPARATOR:
            if any(l.strip() for l in current):
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if any(l.strip() for l in current):
        blocks.append(current)
    return blocks


def is_system_line(text):
    if any(kw in text for kw in SYSTEM_KEYWORDS):
        return True
    if '(' in text and (')' in text or ';' in text):
        return True
    return False


def is_npc_speaker(text):
    """Short line, no dialogue punctuation -> looks like a speaker's name."""
    if text is None or '\n' in text or '{' in text:
        return False
    if '<' in text and ':' in text:
        return False
    if any(text.endswith(p) for p in DIALOGUE_END_PUNCT):
        return False
    if any(c in text for c in DIALOGUE_CHARS):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > 10:
        return False
    if stripped.endswith(SENTENCE_END_SUFFIXES):
        return False
    return True


def classify_block(block_lines):
    while block_lines and block_lines[-1].strip() == '':
        block_lines.pop()
    if not block_lines:
        return None

    jp_lines = [l[1:] for l in block_lines if l.startswith('#')]
    if jp_lines:
        en_lines = [l for l in block_lines if not l.startswith('#')]
        return {'kind': 'pair', 'jp': '\n'.join(jp_lines), 'en': '\n'.join(en_lines)}

    if len(block_lines) == 1:
        single = block_lines[0].strip()
        if TAG_RE.match(single):
            return {'kind': 'tag', 'jp': single, 'en': ''}
        if is_system_line(single):
            return {'kind': 'system', 'jp': single, 'en': ''}

    return {'kind': 'raw', 'jp': '\n'.join(block_lines).strip(), 'en': ''}


# ------------------------------------------------------------ entry build --

def build_entries(classified):
    """Walk classified blocks -> list of entry dicts, resolving speakers."""
    entries = []
    pending_speaker = None  # {'jp': ..., 'en': ...} or None
    n = len(classified)
    i = 0
    while i < n:
        c = classified[i]

        if c['kind'] == 'tag':
            pending_speaker = {'jp': c['jp'], 'en': MAIN_CHAR_NAMES.get(c['jp'], '')}
            i += 1
            continue

        if c['kind'] == 'system':
            entries.append({'section': 'system', 'jp': c['jp'], 'en': c['jp'],
                             'speaker_jp': None, 'speaker_en': None})
            pending_speaker = None
            i += 1
            continue

        # pair or raw: possibly itself a speaker-name for what follows
        if pending_speaker is None and c['kind'] == 'pair' and is_npc_speaker(c['jp']):
            nxt = classified[i + 1] if i + 1 < n else None
            is_duplicate = nxt is not None and nxt.get('jp') == c['jp'] and nxt.get('kind') == 'pair'
            next_is_dialogue = (
                nxt is not None and not is_duplicate and nxt['kind'] in ('pair', 'raw')
                and not (nxt['kind'] == 'pair' and is_npc_speaker(nxt['jp']))
            )
            if next_is_dialogue:
                pending_speaker = {'jp': c['jp'], 'en': c['en']}
                i += 1
                continue

        section = 'dialogue' if pending_speaker else 'misc'
        entries.append({
            'section': section, 'jp': c['jp'], 'en': c['en'],
            'speaker_jp': pending_speaker['jp'] if pending_speaker else None,
            'speaker_en': pending_speaker['en'] if pending_speaker else None,
        })
        pending_speaker = None
        i += 1

    return entries


# -------------------------------------------------------------- XML build --

def build_xml(entries, friendly_name):
    root = ET.Element('SceneText')
    ET.SubElement(root, 'FriendlyName').text = friendly_name

    # --- Speakers: a real <Speakers> element, not a Strings section named
    # "Speaker". Confirmed by disassembling TranslationApp.exe's fMain: its
    # "Speaker" tab (lbSpeaker / CurrentSpeakerList) is populated directly
    # from XMLFile.Speakers, which TranslationLib only fills in from a
    # genuine <Speakers> root element (see GetXmlSpeakerElement / LoadXML).
    # A Strings section literally named "Speaker" does NOT feed that tab. ---
    speaker_ids = {}
    speakers_el = ET.SubElement(root, 'Speakers')
    ET.SubElement(speakers_el, 'Section').text = 'Speaker'
    next_speaker_id = 1
    for e in entries:
        spk = e['speaker_jp']
        if spk and spk not in speaker_ids:
            speaker_ids[spk] = next_speaker_id
            entry_el = ET.SubElement(speakers_el, 'Entry')
            ET.SubElement(entry_el, 'Id').text = str(next_speaker_id)
            ET.SubElement(entry_el, 'JapaneseText').text = spk
            ET.SubElement(entry_el, 'EnglishText').text = e['speaker_en'] or ''
            ET.SubElement(entry_el, 'Status').text = 'To Do'
            next_speaker_id += 1

    entry_id = 1

    def add_strings_block(section_name, section_entries):
        nonlocal entry_id
        strings_el = ET.SubElement(root, 'Strings')
        ET.SubElement(strings_el, 'Section').text = section_name
        for e in section_entries:
            entry_el = ET.SubElement(strings_el, 'Entry')
            ET.SubElement(entry_el, 'Id').text = str(entry_id)
            ET.SubElement(entry_el, 'JapaneseText').text = e['jp']
            ET.SubElement(entry_el, 'EnglishText').text = e['en']
            if e['speaker_jp'] in speaker_ids:
                ET.SubElement(entry_el, 'SpeakerId').text = str(speaker_ids[e['speaker_jp']])
            status = 'Done' if section_name == 'system' else ('Editing' if e['en'] else 'To Do')
            ET.SubElement(entry_el, 'Status').text = status
            entry_id += 1

    # Text entries still live in <Strings> blocks -- just without a
    # duplicate "Speaker" section, since that's what the real <Speakers>
    # element above is for.
    main_entries = [e for e in entries if e['section'] in ('misc', 'dialogue')]
    add_strings_block('Main', main_entries)
    system_entries = [e for e in entries if e['section'] == 'system']
    add_strings_block('system', system_entries)

    return root


def prettify(elem):
    rough = ET.tostring(elem, encoding='unicode')
    reparsed = minidom.parseString(rough.encode('utf-8'))
    return reparsed.toprettyxml(indent='  ', encoding='utf-8').decode('utf-8')


# ----------------------------------------------------------------- driver --

def convert_file(filepath, out_path):
    blocks = parse_blocks(filepath)
    classified = [c for c in (classify_block(b) for b in blocks) if c is not None]
    if not classified:
        print(f"  no content found in {filepath}")
        return

    # FriendlyName = English half of the very first block (the location banner).
    first = classified[0]
    friendly_name = first['en'].strip() if first.get('en') else first['jp'].strip()

    entries = build_entries(classified)
    root = build_xml(entries, friendly_name)
    xml_str = prettify(root)
    # Drop the XML declaration line per the confirmed reference format.
    xml_str = xml_str.split('\n', 1)[1] if xml_str.startswith('<?xml') else xml_str
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(xml_str)
    print(f"  -> {out_path} ({len(entries)} entries, FriendlyName='{friendly_name}')")


def process_folder(input_folder, output_folder):
    os.makedirs(output_folder, exist_ok=True)
    files = [f for f in os.listdir(input_folder) if f.endswith('.txt')]
    print(f"Found {len(files)} .txt files")
    for fname in sorted(files):
        fpath = os.path.join(input_folder, fname)
        print(f"Processing {fname}...")
        out_name = os.path.splitext(os.path.splitext(fname)[0])[0] + '.xml'
        out_path = os.path.join(output_folder, out_name)
        convert_file(fpath, out_path)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python txt_to_translationapp_xml.py <input_folder> <output_folder>")
        print("   or: python txt_to_translationapp_xml.py <single_file.txt> <output.xml>")
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    if os.path.isdir(src):
        process_folder(src, dst)
    else:
        convert_file(src, dst)