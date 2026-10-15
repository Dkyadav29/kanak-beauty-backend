"""
Project package. Installs PyMySQL as the MySQLdb driver so Django's MySQL
backend can talk to MariaDB without a compiled client library. In production
you may install `mysqlclient` instead; this shim is skipped automatically if it
is present.
"""

try:  # pragma: no cover - driver bootstrap
    import MySQLdb  # noqa: F401  (mysqlclient installed)
except ImportError:  # pragma: no cover
    import pymysql

    pymysql.version_info = (2, 2, 7, "final", 0)  # satisfy Django's minimum-version check
    pymysql.install_as_MySQLdb()
