import struct

def patch_character_names(slps_path):
    """
    Patches character names in SLPS_251.72 to display correctly
    on the status screen after English font insertion.
    Relocates names from game-encoded format to ASCII at 0xFAB10.
    Apply this AFTER running updateBlock.py but BEFORE Apache3 reinsertion.
    """
    with open(slps_path, "rb") as f:
        data = bytearray(f.read())

    name_location = 0xFAB10
    names = ["Kyle", "Reala", "Loni", "Judas", "Nanaly", "Harold"]
    char_ptrs = {
        "Kyle":   0xB8000,
        "Reala":  0xB7FFC,
        "Loni":   0xB7FF8,
        "Judas":  0xB7FF4,
        "Nanaly": 0xB7FF0,
        "Harold": 0xB8018,
    }

    # Write ASCII name strings into free space at 0xFAB10
    offset = 0
    name_offsets = {}
    for name in names:
        file_off = name_location + offset
        name_offsets[name] = file_off
        encoded = name.encode("ascii") + b"\x00"
        data[file_off:file_off+len(encoded)] = encoded
        offset += len(encoded)
        print("  Wrote '{}' at file:{}".format(name, hex(file_off)))

    # Update pointers to point to new ASCII name locations
    for name, ptr_addr in char_ptrs.items():
        new_ram_addr = name_offsets[name] + 0xFF000
        old_val = struct.unpack("<I", data[ptr_addr:ptr_addr+4])[0]
        struct.pack_into("<I", data, ptr_addr, new_ram_addr)
        print("  Updated {} ptr@{}: {} -> {}".format(
            name, hex(ptr_addr), hex(old_val), hex(new_ram_addr)))

    with open(slps_path, "wb") as f:
        f.write(data)

    print("\nDone. File size unchanged: {} bytes".format(len(data)))

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "SLPS_251.72"
    print("Patching character names in {}...".format(path))
    patch_character_names(path)
