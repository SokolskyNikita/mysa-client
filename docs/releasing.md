# Releasing

Release the library before updating the Home Assistant dependency pin.
Library releases and integration releases have independent version numbers.

## GitHub publishing setup

To enable the manual GitHub publishing workflow, configure a PyPI Trusted Publisher with
these exact GitHub settings:

| Setting | Value |
| --- | --- |
| Owner | `SokolskyNikita` |
| Repository | `mysa-client` |
| Workflow filename | `release.yml` |
| GitHub environment | `pypi` |

The repository workflow uses GitHub's OIDC identity token through PyPI Trusted
Publishing. Do not add a long-lived PyPI API token to repository secrets.

## Publish sequence

1. Update `[project].version` in `pyproject.toml` and add the user-facing
   changes to `CHANGELOG.md`. Review `docs/api-coverage.md` against the release,
   update its client baseline/review date, and regenerate API summaries and the
   callable index as described in [documentation maintenance](maintenance.md#offline-checks).
2. Run the full CI workflow. It checks Python versions 3.11–3.14, protocol and
   lifecycle tests, examples' offline help paths, documentation links, and
   built distribution contents.
3. Merge the validated library changes. Create an annotated `v<version>` tag
   on the exact commit that passed CI, such as `v0.1.0`.
4. In GitHub Actions, manually dispatch **Publish client** and provide that
   existing tag. The workflow reruns CI, checks that the tag matches the
   package version and validated commit, builds the distributions, and
   publishes through Trusted Publishing in the `pypi` environment.
5. After the package release is visible on PyPI, update the
   [Home Assistant integration](https://github.com/SokolskyNikita/mysa-homeassistant)
   to depend on that exact tested version. Run its complete regression suite
   before releasing the integration.

Do not pin or distribute the integration's external dependency before the
client package release is available. The release workflow does not create a
tag, merge changes, or publish automatically on a push.

For a maintainer publishing locally with an existing PyPI upload credential,
run the same checks and build steps, then `python -m twine upload dist/*`.
Keep that credential outside the checkout. It must authorize this project or
new-project creation; a token scoped to a different project cannot publish it.
GitHub Trusted Publishing requires the setup above.
