# Sanchay AI — Windows Development Setup Guide

This guide provides verified step-by-step instructions for setting up and running **Sanchay AI** on **Windows (PowerShell)**.

> **Current Status Note:** **Phase 1 (Project Foundation, Configuration, Database, and Authentication)** is implemented and verified. Phases 2–8 will be added sequentially as approved.

---

## 1. Prerequisites

Ensure the following software is installed on your Windows machine:

1. **Python 3.11+ (64-bit; verified on Python 3.14.3):**
   ```powershell
   python --version
   ```
2. **PostgreSQL 15+ for Windows (verified on PostgreSQL 18.3):**
   - Ensure the PostgreSQL Windows service is running:
     ```powershell
     Get-Service postgresql*
     ```
3. **Git for Windows:**
   ```powershell
   git --version
   ```

---

## 2. Repository & Virtual Environment Setup

Open **Windows PowerShell** in the project root (`d:\Ashutosh_01\Sanchay AI`):

```powershell
# 1. Create a Python virtual environment in .venv
python -m venv .venv

# 2. Activate the virtual environment in PowerShell
# (If ExecutionPolicy blocks activation, run: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser)
.\.venv\Scripts\Activate.ps1

# 3. Install pinned Phase 1 dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. PostgreSQL Database Creation

Create two local PostgreSQL databases—one for development (`sanchay_ai_dev`) and an isolated one for automated `pytest` execution (`sanchay_ai_test`):

```powershell
# Connect to PostgreSQL and create the development and test databases
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE sanchay_ai_dev ENCODING 'UTF8';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE sanchay_ai_test ENCODING 'UTF8';"
```

---

## 4. Environment Variable Configuration

1. Copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
2. Generate a secure 64-character hex `SECRET_KEY`:
   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
3. Open `.env` in your editor and update:
   - `SECRET_KEY=<your-generated-hex-key>`
   - `DATABASE_URL=postgresql+psycopg2://postgres:<your_password>@localhost:5432/sanchay_ai_dev`
   - `TEST_DATABASE_URL=postgresql+psycopg2://postgres:<your_password>@localhost:5432/sanchay_ai_test`
   - Keep `AI_ENABLED=false` throughout Phases 1–7.

---

## 5. Database Connectivity & Migrations (Flask-Migrate / Alembic)

```powershell
# Verify PostgreSQL connection and server version
python -m flask --app wsgi:app db-check

# Apply Phase 1 schema migrations (0001_phase1_users_audit)
python -m flask --app wsgi:app db upgrade

# Check current migration revision
python -m flask --app wsgi:app db current
```

---

## 6. Creating an Initial User Account (`flask create-user`)

In compliance with **Project Rule #11** and **Project Rule #21**, no user credentials are hardcoded in the application. Create your initial development user via the safe CLI command (it will prompt for the password without echoing to the terminal):

```powershell
python -m flask --app wsgi:app create-user `
  --username admin_rajesh `
  --email rajesh@sanchay.local `
  --full-name "Rajesh Sharma" `
  --role SUPER_ADMIN
```

---

## 7. Running the Application Locally

```powershell
# Start the Flask development server on http://127.0.0.1:5000
python -m flask --app wsgi:app run --host=127.0.0.1 --port=5000
```

Open your browser at `http://127.0.0.1:5000/auth/login` and sign in with the user created in Step 6.

---

## 8. Running the Automated Test Suite (`pytest`)

```powershell
# Run all Phase 1 tests against the isolated sanchay_ai_test PostgreSQL database
pytest -v

# Run with coverage report
pytest --cov=app --cov-report=term-missing -v
```

---

## 9. Common Windows Troubleshooting

| Symptom | Cause | Resolution |
| :--- | :--- | :--- |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Windows PowerShell default execution policy | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` in PowerShell. |
| `psycopg2.OperationalError: connection to server at "localhost" (::1), port 5432 failed` | PostgreSQL service stopped or wrong password in `.env` | Start service (`Start-Service postgresql*`) and verify password in `DATABASE_URL`. |
| `alembic.util.exc.CommandError: Target database is not up to date` | Pending migration revisions | Run `python -m flask db upgrade` before generating a new migration. |
| `400 Bad Request: The CSRF token is missing` on `fetch()` calls | Missing `X-CSRFToken` header in vanilla JS request | Ensure `api.js` reads `<meta name="csrf-token">` and attaches `X-CSRFToken`. |
