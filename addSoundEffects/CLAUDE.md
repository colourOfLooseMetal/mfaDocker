# addSoundEffects

Adding manually-curated sound effect clips (audience laughter, bass riffs,
stings, etc.) to the web n-gram mashup builder, alongside the existing
dialogue line clips in [lineClips/](../lineClips/).

## Goal

Sound effects aren't part of MFA-aligned dialogue, so they can't be detected
or timed automatically the way words/n-grams are. Instead we will:

1. Survey the source `.srt` files to find bracketed/parenthetical sound-effect
   cues (e.g. `[ Audience Laughing ]`, `( Theme Music Playing )`) — these use
   non-spoken-word characters (brackets, parens, asterisks, etc.) that don't
   appear in normal dialogue lines.
2. Manually review and curate a list of sound effect cues worth clipping.
3. Cut short clips for each chosen sound effect (reusing the
   [cutLineClips.py](../cutLineClips.py) extraction approach — 720x540,
   normalized) into the end of [lineClips/](../lineClips/), named so they
   don't collide with existing `sfvL_<N>.mp4` dialogue clip IDs.
4. Extend [lineClips/index.json](../lineClips/index.json) so the web n-gram
   picker (`web/js/ffmpeg.js` → `joinSegments`) can include sound effect
   clips as selectable entries alongside dialogue n-grams.

## Status

- [ ] Step 1: survey `.srt` files for non-standard characters
      ([findSoundEffectChars.py](findSoundEffectChars.py)) — in progress
- [ ] Step 2: curate sound effect cue list from survey output
- [ ] Step 3: cut sound effect clips
- [ ] Step 4: update `lineClips/index.json` with sound effect entries

## Files

```
CLAUDE.md                  — this file
findSoundEffectChars.py    — scans Seinfeld/srts/*.srt, reports characters
                             outside the normal spoken-word set (letters,
                             digits, standard punctuation, '$'); optional
                             toggle to print every line containing one
```

## Notes

- Source `.srt` files live in `Seinfeld/srts/sXXeYY.srt` (gitignored, one
  flat folder per [CLAUDE.md](../CLAUDE.md) layout).
- "Normal" characters = letters, digits, whitespace, and standard spoken-word
  punctuation (`.`, `,`, `'`, `"`, `-`, `!`, `?`, `:`, `;`, `...`, etc.) plus
  `$` (used for currency, already handled by
  [sanatizeSRTsaveToTxt.py](../sanatizeSRTsaveToTxt.py)). Anything else
  (brackets, parens, music notes, asterisks, etc.) is a candidate marker for
  sound-effect/stage-direction cues.
