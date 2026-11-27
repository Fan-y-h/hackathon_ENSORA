# hackathon_ENSORA
Améliorons l'ensora, améliorons la vie des étudiant !

## Run the P1 backend

From the shared checkout, run:

```bash
cd ~/hackathon_ENSORA
python3 scripts/start.py
```

The launcher reuses the existing virtual environment, installs missing Python dependencies, prepares private runtime directories, offers source onboarding in an interactive terminal and serves the local API at http://127.0.0.1:8765/docs. Use `--skip-onboarding` to start the API directly.

For offline verification and source diagnostics:

```bash
python3 scripts/bootstrap.py --test --browser
.venv/bin/python -m pytest backend/tests
python3 scripts/check_connections.py
```

See [P1 setup, scope and API](docs/DATA_ENGINEERING.md#p1-local-runtime).
