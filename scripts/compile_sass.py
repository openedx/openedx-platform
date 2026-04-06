#!/usr/bin/env python
"""
Defines a CLI for compiling Sass (both default and themed) into CSS.

Should be run from the root of edx-platform using `npm run` wrapper.
Requirements for this scripts are stored in requirements/edx/assets.in.

Get more details:

     npm run compile-sass -- --help
     npm run compile-sass -- --dry

Setup (Tutor and Devstack will do this for you):

    python -m venv venv
    . venv/bin/activate
    pip install -r requirements/edx/assets.txt

Usage:

     npm run compile-sass             # prod, no args
     npm run compile-sass -- ARGS     # prod, with args
     npm run compile-sass-dev         # dev, no args
     npm run compile-sass-dev -- ARGS # dev, with args

This script is intentionally implemented in a very simplistic way. It prefers repetition
over abstraction, and its dependencies are minimal (just click and libsass-python, ideally).
We do this because:

* If and when we migrate from libsass-python to something less ancient like node-sass or dart-sass,
  we will want to re-write this script in Bash or JavaScript so that it can work without any
  backend tooling. By keeping the script dead simple, that will be easier.

* The features this script supports (legacy frontends & comprehensive theming) are on the way out,
  in favor of micro-frontends, branding, and Paragon design tokens. We're not sure how XBlock
  view styling will fit into that, but it probably can be much simpler than comprehensive theming.
  So, we don't need this script to be modular and extensible. We just need it to be obvious, robust,
  and easy to maintain until we can delete it.

See docs/decisions/0017-reimplement-asset-processing.rst for more details.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

import click

# Accept both long- and short-forms of these words, but normalize to long form.
# We accept both because edx-platform asset build scripts historically use the short form,
# but NODE_ENV uses the long form, so to make them integrate more seamlessly we accept both.
NORMALIZED_ENVS = {
    "prod": "production",
    "dev": "development",
    "production": "production",
    "development": "development",
}


@click.option(
    "-T",
    "--theme-dir",
    "theme_dirs",
    metavar="PATH",
    multiple=True,
    envvar="COMPREHENSIVE_THEME_DIRS",
    type=click.Path(path_type=Path),
    help=(
        "Consider sub-dirs of PATH as themes. "
        "Multiple theme dirs are accepted. "
        "If none are provided, we look at colon-separated paths on the COMPREHENSIVE_THEME_DIRS env var."
    ),
)
@click.option(
    "-t",
    "--theme",
    "themes",
    metavar="NAME",
    multiple=True,
    type=str,
    help=(
        "A theme to compile. "
        "NAME should be a sub-dir of a PATH provided by --theme-dir. "
        "Multiple themes are accepted. "
        "If none are provided, all available themes are compiled."
    ),
)
@click.option(
    "--skip-default",
    is_flag=True,
    help="Don't compile default Sass.",
)
@click.option(
    "--skip-themes",
    is_flag=True,
    help="Don't compile any themes (overrides --theme* options).",
)
@click.option(
    "--skip-lms",
    is_flag=True,
    help="Don't compile any LMS Sass.",
)
@click.option(
    "--skip-cms",
    is_flag=True,
    help="Don't compile any CMS Sass.",
)
@click.option(
    "--env",
    type=click.Choice(["dev", "development", "prod", "production"]),
    default="prod",
    help="Optimize CSS for this environment. Defaults to 'prod'.",
)
@click.option(
    "--dry",
    is_flag=True,
    help="Print what would be compiled, but don't compile it.",
)
@click.option(
    "-h",
    "--help",
    "show_help",
    is_flag=True,
    help="Print this help.",
)
@click.command()
@click.pass_context
def main(
    context: click.Context,
    theme_dirs: list[Path],
    themes: list[str],
    skip_default: bool,
    skip_themes: bool,
    skip_lms: bool,
    skip_cms: bool,
    env: str,
    dry: bool,
    show_help: bool,
) -> None:
    """
    Compile Sass for edx-platform and its themes.

    Default Sass is compiled unless explicitly skipped.
    Additionally, any number of themes may be specified using --theme-dir and --theme.

    Default CSS is compiled to css/ directories in edx-platform.
    Themed CSS is compiled to css/ directories in their source themes.
    """

    def compile_sass_dir(
        message: str,
        source_root: Path,
        target_root: Path,
        includes: list[Path],
        tolerate_missing: bool = False,
    ) -> None:
        """
        Compile a directory of Sass into a target CSS directory, and generate any missing RTL CSS.
        Structure of source dir is mirrored in target dir.

        IMPLEMENTATION NOTES:
        =====================

        libsass is a C++ library for compiling Sass (ref: https://github.com/sass/libsass).

        libsass-python is a small PyPI package wrapping libsass, including:
          * The `_sass` module, which provides direct Python bindings for the C++ library.
            (ref: https://github.com/sass/libsass-python/blob/0.10.0/pysass.cpp)
          * The `sass` module, which adds some friendly Pythonic wrapper functions around `_sass`,
            notably `sass.compile_dirname(...)`.
            (ref: https://github.com/sass/libsass-python/blob/0.10.0/sass.py#L198-L201)

        Our legacy Sass code only works with a super old version of libsass (3.3.2,) which is provided to us by a super
        old version of libsass-python (0.10.0). In this super old libsass-python version:
          * the `sass` module DOESN'T support Python 3.11+, but
          * the `_sass` module DOES support Python 3.11+.

        Upgrading our Sass to work with newer a libsass version would be arduous and would potentially break
        comprehensive themes, so we don't want to do that. Forking libsass-python at v0.10.0 and adding Python 3.11+
        support would mean adding another repo to the openedx org. Rather than do either of those, we've decided to
        hack around the problem by just reimplementing what we need of `sass.compile_dirname` here, directly on top
        of the `_sass` C++ binding module.

        Eventually, we may eschew libsass-python altogether by switching to SassC@3.3.2, a direct CLI for libsass@3.3.2.
        (ref: https://github.com/sass/sassc). This would be nice because it would allow us to remove Python from the
        Sass build pipeline entirely. However, it would mean explicitly compiling & installing both libsass and SassC
        within the edx-platform build environment, which has its own drawbacks.
        """
        # Constants from libsass-python
        SASS_STYLE_NESTED = 0
        _SASS_STYLE_EXPANDED = 1
        _SASS_STYLE_COMPACT = 2
        SASS_STYLE_COMPRESSED = 3
        SASS_COMMENTS_NONE = 0
        SASS_COMMENTS_LINE_NUMBERS = 1

        # Defaults from libass-python
        precision = 5
        source_map_filename = None
        custom_functions = []
        importers = None

        use_dev_settings: bool = NORMALIZED_ENVS[env] == "development"
        fs_encoding: str = sys.getfilesystemencoding() or sys.getdefaultencoding()
        output_style: int = SASS_STYLE_NESTED if use_dev_settings else SASS_STYLE_COMPRESSED
        source_comments: int = SASS_COMMENTS_LINE_NUMBERS if use_dev_settings else SASS_COMMENTS_NONE
        include_paths: bytes = os.pathsep.join(str(include) for include in includes).encode(fs_encoding)

        click.secho(f"  {message}...", fg="cyan")
        click.secho(f"    Source: {source_root}")
        click.secho(f"    Target: {target_root}")
        if not source_root.is_dir():
            if tolerate_missing:
                click.secho(f"    Skipped because source directory does not exist.", fg="yellow")  # noqa: F541
                return
            else:
                raise FileNotFoundError(f"missing Sass source dir: {source_root}")
        click.echo(f"    Include paths:")  # noqa: F541
        for include in includes:
            click.echo(f"      {include}")

        click.echo(f"    Files:")  # noqa: F541
        for dirpath, _, filenames in os.walk(str(source_root)):
            for filename in filenames:
                if filename.startswith('_'):
                    continue
                if not filename.endswith(('.scss', '.sass')):
                    continue
                source = Path(dirpath) / filename
                target = (target_root / source.relative_to(source_root)).with_suffix('.css')
                click.echo(f"      {source} -> {target}")
                if not dry:
                    # Import _sass late so that this script can be dry-run without installing
                    # libsass, which takes a while as it must be compiled from its C source.
                    from _sass import compile_filename  # pylint: disable=protected-access
                    success, output, _ = compile_filename(
                        str(source).encode(fs_encoding),
                        output_style,
                        source_comments,
                        include_paths,
                        precision,
                        source_map_filename,
                        custom_functions,
                        importers,
                    )
                    output_text = output.decode('utf-8')
                    if not success:
                        raise Exception(f"Failed to compile {source}: {output_text}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, 'w', encoding="utf-8") as target_file:
                        target_file.write(output_text)

        click.secho(f"    Done.", fg="green")  # noqa: F541
        # For Sass files without explicit RTL versions, generate
        # an RTL version of the CSS using the rtlcss library.
        for sass_path in glob.glob(str(source_root) + "/**/*.scss"):
            if Path(sass_path).name.startswith("_"):
                # Don't generate RTL CSS for partials
                continue
            if sass_path.endswith("-rtl.scss"):
                # Don't generate RTL CSS if the file is itself an RTL version
                continue
            if Path(sass_path.replace(".scss", "-rtl.scss")).exists():
                # Don't generate RTL CSS if there is an explicit Sass version for RTL
                continue
            click.echo("    Generating missing right-to-left CSS:")
            source_css_file = sass_path.replace(str(source_root), str(target_root)).replace(
                ".scss", ".css"
            )
            target_css_file = source_css_file.replace(".css", "-rtl.css")
            click.echo(f"      Source: {source_css_file}")
            click.echo(f"      Target: {target_css_file}")
            if not dry:
                subprocess.run(["rtlcss", source_css_file, target_css_file])
            click.secho("      Generated.", fg="green")

    # Information
    click.secho(f"USING ENV: {NORMALIZED_ENVS[env]}", fg="blue")
    if dry:
        click.secho(f"DRY RUN: Will print compile steps, but will not compile anything.", fg="blue")  # noqa: F541
    click.echo()

    # Warnings
    if show_help:
        click.echo(context.get_help())
        return
    if skip_lms and skip_cms:
        click.secho("WARNING: You are skipping both LMS and CMS... nothing will be compiled!", fg="yellow")
    if skip_default and skip_themes:
        click.secho("WARNING: You are skipped both default Sass and themed Sass... nothing will be compiled!", fg="yellow")
    click.echo()

    # Build a list of theme paths:
    if skip_themes:
        theme_paths = []
    else:
        theme_paths = [
            theme_dir / theme
            # For every theme dir,
            for theme_dir in theme_dirs
            for theme in (
                # for every theme name (if theme names provided),
                themes
                or
                # or for every subdir of theme dirs (if no theme name provided),
                (theme_dir_entry.name for theme_dir_entry in theme_dir.iterdir())
            )
            # consider the path a theme if it has a lms/ or cms/ subdirectory.
            if (theme_dir / theme / "lms").is_dir() or (theme_dir / theme / "cms").is_dir()
        ]

    # We expect this script to be run from the edx-platform root.
    repo = Path(".")
    if not (repo / "xmodule").is_dir():
        # Sanity check: If the xmodule/ folder is missing, we're definitely not at the root
        # of edx-platform, so save the user some headache by exiting early.
        raise Exception(f"{__file__} must be run from the root of edx-platform")

    # Every Sass compilation will use have these directories as lookup paths,
    # regardless of theme.
    common_includes = [
        repo / "common" / "static",
        repo / "common" / "static" / "sass",
        repo / "node_modules" / "@edx",
        repo / "node_modules",
    ]

    if not skip_default:
        click.secho(f"Compiling default Sass...", fg="cyan", bold=True)  # noqa: F541
        if not skip_lms:
            compile_sass_dir(
                "Compiling default LMS Sass",
                repo / "lms" / "static" / "sass",
                repo / "lms" / "static" / "css",
                includes=[
                    *common_includes,
                    repo / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass",
                ],
            )
            compile_sass_dir(
                "Compiling default certificate Sass",
                repo / "lms" / "static" / "certificates" / "sass",
                repo / "lms" / "static" / "certificates" / "css",
                includes=[
                    *common_includes,
                    repo / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass",
                ],
            )
        if not skip_cms:
            compile_sass_dir(
                "Compiling default CMS Sass",
                repo / "cms" / "static" / "sass",
                repo / "cms" / "static" / "css",
                includes=[
                    *common_includes,
                    repo / "lms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass",
                ],
            )
        click.secho(f"Done compiling default Sass!", fg="cyan", bold=True)  # noqa: F541
        click.echo()

    for theme in theme_paths:
        click.secho(f"Compiling Sass for theme at {theme}...", fg="cyan", bold=True)
        if not skip_lms:
            compile_sass_dir(
                "Compiling default LMS Sass with themed partials",
                repo / "lms" / "static" / "sass",
                theme / "lms" / "static" / "css",
                includes=[
                    *common_includes,
                    theme / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass",
                ],
                tolerate_missing=True,
            )
            compile_sass_dir(
                "Compiling themed LMS Sass as overrides to CSS from previous step",
                theme / "lms" / "static" / "sass",
                theme / "lms" / "static" / "css",
                includes=[
                    *common_includes,
                    theme / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass" / "partials",
                    repo / "lms" / "static" / "sass",
                ],
                tolerate_missing=True,
            )
            compile_sass_dir(
                "Compiling themed certificate Sass",
                theme / "lms" / "static" / "certificates" / "sass",
                theme / "lms" / "static" / "certificates" / "css",
                includes=[
                    *common_includes,
                    theme / "lms" / "static" / "sass" / "partials",
                    theme / "lms" / "static" / "sass",
                ],
                tolerate_missing=True,
            )
        if not skip_cms:
            compile_sass_dir(
                "Compiling default CMS Sass with themed partials",
                repo / "cms" / "static" / "sass",
                theme / "cms" / "static" / "css",
                includes=[
                    *common_includes,
                    repo / "lms" / "static" / "sass" / "partials",
                    theme / "cms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass",
                ],
                tolerate_missing=True,
            )
            compile_sass_dir(
                "Compiling themed CMS Sass as overrides to CSS from previous step",
                theme / "cms" / "static" / "sass",
                theme / "cms" / "static" / "css",
                includes=[
                    *common_includes,
                    repo / "lms" / "static" / "sass" / "partials",
                    theme / "cms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass" / "partials",
                    repo / "cms" / "static" / "sass",
                ],
                tolerate_missing=True,
            )
        click.secho(f"Done compiling Sass for theme at {theme}!", fg="cyan", bold=True)
        click.echo()

    # Report what we did.
    click.secho("Successfully compiled:", fg="green", bold=True)
    if not skip_default:
        click.secho(f"  - {repo.absolute()} (default Sass)", fg="green")
    for theme in theme_paths:
        click.secho(f"  - {theme}", fg="green")
    if skip_lms:
        click.secho(f"(skipped LMS)", fg="yellow")  # noqa: F541
    if skip_cms:
        click.secho(f"(skipped CMS)", fg="yellow")  # noqa: F541


if __name__ == "__main__":
    main(prog_name="npm run compile-sass --")
