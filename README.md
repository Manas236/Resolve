# Resolve

Resolve is a headless pipeline under development for English-language faceless gym motivation reels. The intended flow is source → verify → cut → render → review → publish → measure.

## Develop locally

Python 3.11 or newer is required. The current scaffold uses the standard library. From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 -m resolve_pipeline
```

The startup command reads an optional .env and creates the local state directories. Copy .env.example to .env for overrides. Use one literal KEY=value per line with no shell quoting or expansion. Process environment takes precedence. Relative state paths resolve from the environment file's directory. Keep state outside git. Startup sends no messages and publishes no media.

| Path | Contents |
| --- | --- |
| resolve_pipeline/ | Runtime code |
| tests/ | Automated tests |
| state/downloads/ | Isolated downloaded media |
| state/media/ | Runtime assets and renders |
| state/cache/ | Local cache |

For current milestone evidence and the next action, read [PROGRESS.md](PROGRESS.md), [DECISIONS.md](DECISIONS.md), [RESEARCH.md](RESEARCH.md) and [NEEDS_MANAS.md](NEEDS_MANAS.md).
