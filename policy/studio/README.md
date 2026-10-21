# Store Picker Bot app

Desktop app to record and review demonstrations without a terminal (PC only).

| File | What it does |
|---|---|
| `install_desktop_icon.sh` | Adds the app to the applications menu and the desktop (once, by the admin) |
| `run_studio.sh` | Starts the app (what the icon runs) |
| `studio.py` | The app's local server; runs `data_collection/record.py --studio` for each round |
| `dataset_view.py` | Episodes, videos, joint data and review decisions of a dataset |
| `studio_config.json` | Settings: default plan, long-episode warning, minimum free disk space |
| `web/` | The app's pages |

Guide: [DATA_STUDIO.md](../docs/DATA_STUDIO.md)
