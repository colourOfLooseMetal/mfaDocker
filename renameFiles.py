import os
import re

# --- CONFIG ---
SEASONS = range(2, 10)   # Season2 .. Season9 (Season1 already renamed)
TEST_MODE = False
# --------------

EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")

def run(directory):
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a valid directory.")
        return

    files = [
        f for f in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, f))
    ]

    renamed, skipped = 0, 0
    for filename in files:
        m = EP_RE.search(filename)
        if not m:
            print(f"[SKIP]    {filename} — no SxxEyy pattern found.")
            skipped += 1
            continue

        season, episode = int(m.group(1)), int(m.group(2))
        ext = os.path.splitext(filename)[1]
        new_name = f"s{season:02d}e{episode:02d}{ext}"

        if filename == new_name:
            print(f"[SKIP]    {filename} — already named.")
            skipped += 1
            continue

        if TEST_MODE:
            print(f"{filename}  -->  {new_name}")
        else:
            os.rename(os.path.join(directory, filename),
                      os.path.join(directory, new_name))
            print(f"Renamed: {filename}  -->  {new_name}")
        renamed += 1

    print(f"Done. {renamed} renamed, {skipped} skipped.")


for season in SEASONS:
    run(f"./Seinfeld/Season{season}")
