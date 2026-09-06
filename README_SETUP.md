# peddishiva GitHub Profile SVG

Production-ready GitHub profile README + dynamic SVG replica inspired by the Andrew6rant profile-SVG concept, adapted for **Peddi Shiva / peddishiva**.

## What is included

- Dynamic GitHub GraphQL statistics
- Repository count and total stars across all owned repositories (paginated)
- Follower count
- Contribution/repository count
- Authored commit count
- Lines added, deleted, and net LOC from default-branch histories
- SHA-256 per-user repository cache
- Commit-count cache invalidation
- Optional deleted-repository archive support
- Automatic dark/light SVG switching in the profile README
- `profile.json` for editable static profile information
- Pixel/dither portrait in `assets/pixel_portrait.png`
- GitHub Actions automation
- No Projects section in the SVG
- Automatic commit only when generated files actually change

## Edit your profile without editing the SVG

Update `profile.json` whenever you want to change your static information, such as: OS, status, university, location, focus, education, CGPA, batch, interests, tech stack, tools, email, LinkedIn, GitHub, or portfolio.

After changing `profile.json`, run the workflow manually or push to `main`. `today.py` reads the JSON and writes the values into both SVGs.

## Age behavior

DOB is configured as **22-02-2005**. The public SVG displays only integer age, e.g. `21 years`. The calculation changes only on **February 22**, so the age value itself cannot create daily commits.

## GitHub setup

1. Create/use the profile repository: `peddishiva/peddishiva`.
2. Push all package contents to the `main` branch.
3. In **Settings → Secrets and variables → Actions**, add:
   - `ACCESS_TOKEN` — a GitHub token that can read the required GraphQL data.
4. Go to **Actions → Update GitHub Profile SVG → Run workflow** for the first build.
5. The workflow also runs daily at **04:00 UTC**.

The GitHub username is read from `profile.json`, so a separate `USER_NAME` secret is not required.

## Local test

Install dependencies:

```bash
python -m pip install -r cache/requirements.txt
```

Set `ACCESS_TOKEN`, then run:

```bash
python today.py
```

The script updates `dark_mode.svg`, `light_mode.svg`, and the cache.

## Repository structure

```text
peddishiva-github-profile/
├── profile.json
├── today.py
├── dark_mode.svg
├── light_mode.svg
├── assets/
│   └── pixel_portrait.png
├── cache/
│   ├── requirements.txt
│   └── repository_archive.txt
├── .github/
│   └── workflows/
│       └── build.yaml
├── README.md
└── README_SETUP.md
```
