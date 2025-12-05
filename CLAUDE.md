# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is the lab manual for the Contextual Dynamics Laboratory (CDL) at Dartmouth College. It's a LaTeX document using the Tufte-book class that describes lab policies, member responsibilities, and research approach.

## Build Commands

**Compile the PDF:**
```bash
./compile.sh
```

This runs `latex` multiple times (for cross-references and index), then `pdflatex`, and cleans up intermediate files. Requires a LaTeX distribution (e.g., `brew install --cask mactex` on macOS).

**HTML version:** The HTML version is automatically generated via GitHub Actions when changes are pushed to master. No manual build is needed. The workflow uses make4ht (TeX4ht) with `tufte.cfg` configuration to convert to tufte-css styled HTML.

## Project Structure

- `lab_manual.tex` - Main document source (single file containing all content)
- `lab_manual.pdf` - Compiled output (committed to repo)
- `tufte.cfg` - TeX4ht configuration for HTML generation
- `tufte-book.cls`, `tufte-common.def`, `tufte-handout.cls` - Custom Tufte template files
- `lab_logo/` - Lab logo images (PNG, PDF)
- `resources/` - Additional resources (cheatsheets, cluster tutorials)
- `.github/workflows/` - CI/CD workflows (LaTeX validation, HTML generation)

## LaTeX Conventions

The document uses several custom commands defined in `lab_manual.tex`:
- `\marginnote{}` - For margin notes (TASK items and NOTEs)
- `\newthought{}` - For section introductions
- `\ourschool` - Expands to "Dartmouth College"
- `\director`, `\coordinator` - Lab director references
- Links use `dartmouthgreen` color (RGB: 0, 105, 62)

## Workflow Notes

After editing `lab_manual.tex`:
1. Run `./compile.sh` to regenerate the PDF
2. Verify the PDF looks correct (check TOC links, margin notes positioning)
3. Commit both the `.tex` source and updated `.pdf`
