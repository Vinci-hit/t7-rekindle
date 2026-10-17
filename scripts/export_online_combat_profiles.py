"""Export the shared static catalogue for the Rust room authority."""
import argparse
import json
from pathlib import Path
import runpy


def render():
    root = Path(__file__).resolve().parents[1]
    data = runpy.run_path(str(root / 'src/Business/scripts/combat_profile_data.py'))
    profiles = {hero: {weapon: data['PROFILES'][index] for weapon, index in weapons.items()}
                for hero, weapons in data['HERO_WEAPONS'].items()}
    return json.dumps(profiles, ensure_ascii=False, separators=(',', ':')) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render(), encoding='utf-8')
