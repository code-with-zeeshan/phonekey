# Contributing to PhoneKey

Thanks for taking the time to contribute! This guide covers how to
report bugs, suggest features, and submit code changes.

---

## 🐛 Reporting Bugs

Open a [GitHub Issue](https://github.com/yourusername/phonekey/issues/new)
and include:

- PhoneKey version (`python system.py --version` or banner version)
- OS and Python version
- Steps to reproduce
- Expected vs actual behaviour
- Terminal output (redact your PIN if visible)

---

## 💡 Suggesting Features

Open an issue with the `enhancement` label. Describe:
- The problem it solves
- How you'd expect it to work
- Any alternatives you've considered

---

## 🔧 Submitting Code

### Setup

```bash
git clone https://github.com/yourusername/phonekey.git
cd phonekey
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Before Opening a PR

```bash
# Tests pass
python -m pytest test_server.py -v

# No lint errors
ruff check .
ruff format --check .
```

### Pull Request Guidelines

- One feature or fix per PR
- Update `CHANGELOG.md` under `[Unreleased]`
- Update `README.md` if you add/change user-facing behaviour
- Keep `server.py` free of module-level side effects
- Do not add new logging files — everything stays in `logging_setup.py`
- Test imports must work without `pynput` installed

---

## 📐 Architecture Rules

Before contributing code, read the **Module Contracts** section in
[WORKFLOW.md](WORKFLOW.md). The most important rule:

> All runtime initialisation in `server.py` happens inside `main(args)`,
> never at module level.

---

## 📄 License

By contributing, you agree that your contributions will be licensed
under the [MIT License](LICENSE).