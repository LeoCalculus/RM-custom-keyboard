# RoboMaster UI Button Extractor

Run `extract.bat`. Results are written to `output/buttons.json` and
`output/buttons.csv`.

Edit `config.yaml` to change the target screen resolution or resource filters.
Canvas reference resolution and scaling are read from the client scene when the
corresponding values are set to `auto`.

Coordinates use the top-left of the target screen as `(0, 0)`. Inactive buttons
are included by default because many competition controls are enabled only after
the client receives robot or referee-system state.
