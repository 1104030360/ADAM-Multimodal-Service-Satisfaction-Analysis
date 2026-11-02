# README image sources

## Application screenshots

`screenshots/*.png` are captures of the current ADAM QML pages and QtWidgets recorder from the desktop code at `672f972`, taken on November 2, 2026 through `uv run briefcase dev --test` using the project's Qt integration fixtures.

All account names, service records, scores, outcomes, goals, device names, videos, and report content are synthetic examples. MySQL ran in a disposable test container; S3 was simulated with moto. The captures did not use production accounts, cloud resources, cameras, or microphones. Sample videos and camera frames use simple drawn silhouettes with a generated silent audio track; the PDF previews use the project's report renderer with sample inputs and a drawn avatar. No external AI was called for the captures.

The captures preserve the real interface layout. They demonstrate the UI and do not verify recording hardware or the live SQS deployment. Source-code fixes and layout changes should be followed by fresh captures.

The application's artwork, font, and Lucide icon credits remain in [the application asset sources](../../myapp/src/myapp/images/SOURCES.md). Application code is covered by the [BSD 3-Clause License](../../myapp/LICENSE).

## Diagrams

`architecture.svg`, `analysis-pipeline.svg`, and `user-workflow.svg` are original project diagrams based on the current application and analysis-service implementation. Their colors follow the application's `Theme.qml` tokens. Matching PNGs are rendered from those SVG sources at twice their intrinsic resolution with Qt's SVG renderer.

The architecture describes the SQS API/outbox/worker implementation recorded on November 2, 2026. Deployment notes describe a recorded state; the images are not a live service-status display.
