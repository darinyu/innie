# PyPI Release Notes

Innie is published on PyPI as an alpha package. The repo-side release path
builds and validates distributions, smoke-tests the installed wheel, and
publishes through PyPI trusted publishing.

## Release Readiness Tasks

- [x] Declare Apache-2.0 package metadata in `pyproject.toml`.
- [x] Add PyPI-facing project metadata: classifiers, keywords, project URLs,
      and license file metadata.
- [x] Keep `pyproject.toml` and `src/innie/__init__.py` versions in sync with a
      metadata test.
- [x] Build and validate wheel and source distributions in CI.
- [x] Smoke-test the installed wheel in CI.
- [x] Add `CONTRIBUTING.md` and `SECURITY.md`.
- [x] Pick the first public version: `0.1.0`.
- [x] Add release notes in `CHANGELOG.md`.
- [x] Configure a PyPI trusted publisher for
      `darinyu/innie/.github/workflows/publish.yml` with environment `pypi`.
- [x] Create a GitHub release with clear alpha notes.
- [x] Restore the README PyPI badge after the first successful PyPI publish.

## Manual Release Checklist

1. Confirm CI is green on `main`.
2. Confirm the version in `pyproject.toml` and `src/innie/__init__.py` is
   `0.1.0`.
3. Run:

   ```bash
   python3 -m pip install build twine
   python3 -m build
   python3 -m twine check dist/*
   ```

4. Create a GitHub release for the version.
5. Let `.github/workflows/publish.yml` publish through PyPI trusted publishing.
6. Verify:

   ```bash
   pipx install innie
   innie --help
   innie init --skip-slack-setup
   ```
