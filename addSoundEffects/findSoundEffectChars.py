import os
import re
import string

import pysrt

SRT_FOLDER = r"../Seinfeld/srts"

PRINT_MATCHING_LINES = True  # <-- toggle to print every line containing a non-normal character

NORMAL_CHARS = set(string.ascii_letters + string.digits + string.whitespace)
NORMAL_CHARS.update(".,'\"-!?:;…$íóñéáàÁ")


def find_unusual_chars(text):
    return {ch for ch in text if ch not in NORMAL_CHARS}


def main():
    found_chars = set()
    matching_lines = []

    for filename in sorted(os.listdir(SRT_FOLDER)):
        if not filename.lower().endswith(".srt"):
            continue
        path = os.path.join(SRT_FOLDER, filename)
        subs = pysrt.open(path)
        for sub in subs:
            text = sub.text
            unusual = find_unusual_chars(text)
            if unusual:
                found_chars.update(unusual)
                matching_lines.append((filename, sub.index, text))

    print("Unusual characters found:")
    for ch in sorted(found_chars):
        print(f"  {ch!r} (U+{ord(ch):04X})")

    if PRINT_MATCHING_LINES:
        print(f"\nLines containing unusual characters ({len(matching_lines)}):")
        for filename, index, text in matching_lines:
            print(f"[{filename} #{index}] {text!r}")


if __name__ == "__main__":
    main()
