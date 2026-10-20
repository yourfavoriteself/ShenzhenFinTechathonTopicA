# Submission materials

The six materials of the submission, all generated from this repository.

| # | Material | Files | How it is made |
|---|---|---|---|
| 1 | Technical documentation | [`technical_documentation.pdf`](technical_documentation.pdf) (source [`.md`](technical_documentation.md)), architecture diagram [`diagrams/architecture.png`](diagrams/architecture.png) ([`.drawio`](diagrams/architecture.drawio)) | `make docs` |
| 2 | Presentation, ≤ 10 minutes, with speaker notes | [`presentation.pptx`](presentation.pptx), [`presentation.pdf`](presentation.pdf) | `make docs` (`node docs/deck.js`, numbers read from `evidence/`) |
| 3 | Demo video, ≤ 5 minutes | [`media/guardian_demo.mp4`](media/guardian_demo.mp4) (4:55, recorded from the real UI, with an English voiceover); backup with subtitles [`media/guardian_demo_subtitled.mp4`](media/guardian_demo_subtitled.mp4), subtitles [`media/guardian_demo.srt`](media/guardian_demo.srt), voiceover script [`video_script.md`](video_script.md), caption timeline [`media/guardian_demo_captions.json`](media/guardian_demo_captions.json) | `make video` (`scripts/record_demo.py`) |
| 4 | Code with README and tests | the repository; [`../README.md`](../README.md) | `make test` |
| 5 | Security report: permission levels, known risks, Self-Regulatory Convention for Agent Payment Applications and VI / AP2 tables | [`security_report.pdf`](security_report.pdf) (source [`security_report.src.md`](security_report.src.md), filled from `evidence/`) | `make docs` |
| 6 | Execution evidence: sandbox logs and video | [`../evidence/`](../evidence/README.md): `demo_run.log`, `demo_journal.jsonl`, `demo_summary.json`, `trust_report.json`, `invest_report.json`, reports, `alpaca_paper.json` (a live order on an Alpaca paper account); the video above | `make evidence`, `make video` |

The demo video plays on GitHub:

https://github.com/user-attachments/assets/570b7158-37aa-4c9e-aeca-151b6f3a34a3

Also here: [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md), the choices behind the design and what each one cost us.

Screenshots in `media/` are taken by the video script at the key moments and are used by the presentation.

All data is synthetic: the sandbox bank, merchants and people are fictional, domains use the reserved `.example` TLD.
