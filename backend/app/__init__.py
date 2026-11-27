"""Ensora+ backend package."""

import os

# Some Windows machines (Smart App Control / WDAC) block SQLAlchemy's compiled Cython
# extensions ("DLL load failed ... stratégie de contrôle d'application"). The pure-Python
# implementation is functionally identical and fast enough for a local single-user app.
os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")
