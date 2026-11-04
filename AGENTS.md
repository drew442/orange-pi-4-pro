# Repository Guidelines

## Project Structure & Module Organization

This `orange-pi-4-pro` directory starts without source code, tests, assets, build configuration, or Git metadata. Update this guide as the project develops; the paths below are suggested conventions, not existing directories.

- Keep setup instructions and project purpose in `README.md`.
- Place implementation code in `src/`, automated tests in `tests/`, and development scripts in `scripts/`.
- Put hardware notes and operational instructions in `docs/`. Create directories only when needed.

## Build, Test, and Development Commands

No build, test, or local run commands are currently configured. When introducing tooling, document exact commands, prerequisites, and expected results in `README.md` and update this section. Avoid documenting commands such as `npm test` or `make build` until their configuration exists.

For any future Bash scripts, run `bash -n scripts/<name>.sh` to check syntax; this does not validate runtime behavior.

## Coding Style & Naming Conventions

No language, formatter, or linter has been selected. Follow the conventions of the language introduced and commit its formatting configuration alongside the first implementation. Use spaces for indentation unless the file format requires tabs, as Make recipes do. Keep indentation consistent within each file.

Use descriptive names and lowercase, hyphen-separated documentation filenames, such as `board-setup.md`. Keep changes focused and avoid unrelated formatting edits.

## Testing Guidelines

No testing framework or coverage threshold exists yet. Add reproducible tests with new behavior and regression tests with bug fixes. Follow the selected framework’s test naming conventions and document how to run targeted tests.

For hardware-dependent checks, record the board model, operating system, prerequisites, commands, and observed results. Distinguish automated checks from manual verification.

## Commit & Pull Request Guidelines

No Git history is available to establish commit conventions. Use concise, imperative subjects, for example `Add board setup instructions`, and keep commits focused on one change.

Pull requests should describe the purpose, affected files, validation performed, and any remaining limitations. Link relevant issues and include screenshots only when visual behavior changes.
