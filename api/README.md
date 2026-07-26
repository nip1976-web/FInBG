# FinBG API

FastAPI backend for the FinBG financial control system.

The production service runs on the VPS as the `finbg` operating-system user and
connects to the local PostgreSQL database through peer authentication. No
database password is stored in the repository.

Initial endpoints:

- `GET /health`
- `GET /api/dashboard/summary`

Apply the initial database schema:

```bash
psql -d finbg -f sql/001_initial.sql
```
