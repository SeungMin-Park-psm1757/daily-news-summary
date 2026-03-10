from __future__ import annotations

from morning_radio.config import build_parser, load_config
from morning_radio.pipeline import run_pipeline


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args)
    run_dir = run_pipeline(config)
    print(f"Morning radio build completed: {run_dir}")


if __name__ == "__main__":
    main()
