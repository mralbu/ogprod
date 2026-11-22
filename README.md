# ogprod

[![CI](https://github.com/mralbu/ogprod/actions/workflows/ci.yml/badge.svg)](https://github.com/mralbu/ogprod/actions/workflows/ci.yml)

Public oil & gas production data pipeline and geospatial store.

## Installation

```sh
uv tool install git+https://github.com/mralbu/ogprod
```

## Usage

```sh
ogprod --help
ogprod hello Ada
```

`ogprod skill` prints a Markdown guide to the CLI for people and coding agents
(an [agent skill](https://docs.claude.com/en/docs/claude-code/skills)), and
`ogprod skill --install` installs it into `~/.claude/skills/ogprod/`.

### Shell completion

```sh
eval "$(ogprod completion bash)"   # ~/.bashrc
eval "$(ogprod completion zsh)"    # ~/.zshrc
ogprod completion fish > ~/.config/fish/completions/ogprod.fish
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
