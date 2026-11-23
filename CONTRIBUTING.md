# Contributing to Awtrix Studio

Thanks for your interest! Bug reports, ideas and pull requests are all welcome. Writing in English is preferred, and French is fine too.

By taking part, you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Reporting a bug

Open a [bug report](https://github.com/3615nulsi/ha-awtrix-studio/issues/new?template=bug_report.yml) and include:

- the versions of Awtrix Studio and Home Assistant;
- the clock model and firmware (AWTRIX 3 or AWTRIX NG, and which version);
- the **diagnostics** of the clock (Settings → Devices & services → Awtrix Studio → ⋮ → Download diagnostics). They contain no password, address or template.

To report a security problem, **don't open an issue**: follow the [security policy](SECURITY.md).

## Suggesting a feature

Open a [feature request](https://github.com/3615nulsi/ha-awtrix-studio/issues/new?template=feature_request.yml) and describe what you want to show on the clock, rather than how to build it. For anything bigger than a small change, please **discuss it in an issue before writing code**, so we agree on the design first.

## Pull requests

### Before you start

- Start from the latest `main`.
- Keep each pull request to **one topic**: a fix, a feature, or a refactoring. Several small pull requests are much easier to review than one large one.
- **Don't change the version** in `manifest.json` and don't add release notes: the maintainer prepares releases.

### Setting up

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
```

### Checks to run

The same checks run in CI on every pull request:

```bash
pytest                              # Python tests
node --test tests/frontend/*.test.mjs  # panel and preview tests (Node, no npm needed)
ruff check .                        # lint
ruff format --check .               # formatting
```

New behavior comes with tests, and a fix comes with a test that fails without it.

### Rules of the project

- **No automations, no database writes.** The point of Awtrix Studio is to update the clock without filling the recorder. Don't add entities whose state changes often (such as a "current page" or "last update" sensor).
- **Never insert raw HTML in the panel.** Build elements with the `h()` helper, so that text is always inserted as text. No `innerHTML`, no `eval`, no inline event handlers.
- **Treat what users type as text.** In simple mode, validate entities, colors and numbers. Names that end up in MQTT topics only use letters, digits, `-` and `_`.
- **Keep both firmwares in mind.** If a change only works on AWTRIX 3 or only on AWTRIX NG, say so in the pull request. Simple apps should keep working on both.
- **Translate every new string** in English and French: `frontend/i18n.js` for the panel, `translations/en.json` and `translations/fr.json` for Home Assistant.
- **Test on a real clock** when you can, and say which one in the pull request. A browser test with a stubbed `hass` is useful, but say so if that's all you tried.

The design choices of the integration are explained in [docs/DESIGN.md](docs/DESIGN.md) (in French).

### Review

The maintainer reviews pull requests on his free time, so it can take a few days. Pull requests are usually squash-merged, and you keep the authorship of the commit. Contributors are thanked in the release notes.

## License

By contributing, you agree that your contributions are released under the [MIT license](LICENSE) of the project.
