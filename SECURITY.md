# Security

Guardian is a hackathon project that runs on a synthetic sandbox bank. It holds no real money and no real client data.

- The threat model, permission levels, controls, red-team results and the list of known risks are in the [security report](docs/security_report.pdf) (source: [`docs/security_report.src.md`](docs/security_report.src.md)).
- The red-team can be run with `make test` (suite `tests/redteam`) and the reports regenerated with `make evidence`.
- Secrets (API keys) are read from `.env` or the environment and never committed; see [`.env.example`](.env.example). The model gateway refuses prompts that contain secrets, card or account numbers.

If you find a way to make the kernel execute something the client did not sign, please open a private security advisory on this repository or write to the team. A failing test that reproduces it is the most useful report.
