import os
import re

# --- CONFIG ---
DIRECTORY = "./Seinfeld/Season 1"
SEASON = 1
START_EPISODE = 1
TEST_MODE = False
# --------------

def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]

def run():
    files = [
        f for f in os.listdir(DIRECTORY)
        if os.path.isfile(os.path.join(DIRECTORY, f))
    ]
    files.sort(key=natural_sort_key)

    for i, filename in enumerate(files):
        ext = os.path.splitext(filename)[1]
        episode = START_EPISODE + i
        new_name = f"s{SEASON:02d}e{episode:02d}{ext}"

        if TEST_MODE:
            print(f"{filename}  -->  {new_name}")
        else:
            src = os.path.join(DIRECTORY, filename)
            dst = os.path.join(DIRECTORY, new_name)
            os.rename(src, dst)
            print(f"Renamed: {filename}  -->  {new_name}")

run()