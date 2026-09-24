SCHEMA = """
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tracks (
              id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open', request TEXT, control TEXT DEFAULT 'idle',
              branch TEXT, workspace TEXT, issue INTEGER, pr INTEGER, head TEXT,
              verification TEXT, review TEXT, landing TEXT, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
              track TEXT, revision INTEGER, body TEXT, PRIMARY KEY(track,revision));
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, track TEXT NOT NULL, kind TEXT NOT NULL, purpose TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued', generation INTEGER NOT NULL DEFAULT 0,
              owner TEXT, lease REAL, input_revision INTEGER, attempts INTEGER DEFAULT 0,
              created REAL NOT NULL, updated REAL NOT NULL, dedup TEXT UNIQUE, error TEXT);
            CREATE TABLE IF NOT EXISTS attempts (
              id TEXT PRIMARY KEY, task TEXT, generation INTEGER, pid INTEGER, status TEXT,
              started REAL, finished REAL, result TEXT);
            CREATE TABLE IF NOT EXISTS results (
              id TEXT PRIMARY KEY, task TEXT, attempt TEXT UNIQUE, body TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS waits (work TEXT, dependency TEXT, PRIMARY KEY(work,dependency));
            CREATE TABLE IF NOT EXISTS decisions (
              id TEXT PRIMARY KEY, track TEXT, task TEXT, question TEXT, status TEXT,
              revision INTEGER, answer TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, track TEXT, body TEXT, at REAL);
            CREATE TABLE IF NOT EXISTS effects (
              id TEXT PRIMARY KEY, track TEXT, kind TEXT, intent TEXT, receipt TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS watches (
              id TEXT PRIMARY KEY, track TEXT, body TEXT, status TEXT, trigger_key TEXT,
              last_signal TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS findings (
              id TEXT PRIMARY KEY, track TEXT NOT NULL, body TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open', updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS triages (
              id TEXT PRIMARY KEY, track TEXT NOT NULL, input_key TEXT NOT NULL,
              body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS locks (
              resource TEXT PRIMARY KEY, owner TEXT, lease REAL, generation INTEGER DEFAULT 1);
"""
