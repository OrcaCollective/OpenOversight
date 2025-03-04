![](docs/_static/lpl-logo.png)

[![Build Status](https://github.com/OrcaCollective/OpenOversight/actions/workflows/publish.yml/badge.svg)](https://github.com/OrcaCollective/OpenOversight/actions/workflows/publish.yml)
[![Documentation](https://img.shields.io/badge/docs-main-yellow)](https://orcacollective.github.io/OpenOversight/)
[![Latest GHCR.io image version](https://ghcr-badge.herokuapp.com/orcacollective/openoversight/latest_tag?color=%230078D4)](https://ghcr.io/orcacollective/openoversight)
[![Latest GHCR.io image size](https://ghcr-badge.herokuapp.com/orcacollective/openoversight/size?color=%230078D4)](https://ghcr.io/orcacollective/openoversight)

# OpenOversight (Seattle Fork)

OpenOversight is a Lucy Parsons Labs project to improve law enforcement accountability through public and crowdsourced data. We maintain a database of officer demographic information and provide digital galleries of photographs. This is done to help people identify law enforcement officers for filing complaints and in order for the public to see work-related information about law enforcement officers that interact with the public.

This repository represents the **Seattle fork** of the original project. For more info, see the [old README](README_OLD.md) or [the original repository](https://github.com/lucyparsons/OpenOversight/)

## Development

To run the project locally:
1. [Install `just`](https://github.com/casey/just)
2. Run `just fresh-start` to spin up the database and insert the initial data. **This will create a local super-admin user for you** with the following information:
   1. `Username`: `admin`
   2. `Email`: `admin@admin.com`
   3. `Password`: `admin`
3. Visit http://localhost:3000!

To re-generate the poetry lock file (if you need to update dependencies in pyproject.toml):
1. Run `just lock`
2. Include the modified `poetry.lock` file in your commit

## Testing

To run tests, run `just test` once your containers are up and running.

We use Playwright for functional testing. If functional tests fail locally, you can find per-test traces in the `build/test-results` directory. In the Lint and Test GitHub action, traces are uploaded as an artifact you can download via the "Upload Artifact" step. Once you have your trace, you can upload it to the [Playwright Trace Viewer](https://trace.playwright.dev/) tool for debugging.


## Deployment

Please see the [DEPLOY.md](/DEPLOY.md) file for deployment instructions.

The same `just` commands can be used to deploy production.
The `docker-compose-prod.yml` file defines the minimum services for running in production.
In order to use this file, set the `IS_PROD` environment variable to `true`.
This will run all commands using the prod docker file.
