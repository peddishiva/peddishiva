import base64
import datetime
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from lxml import etree

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
PROFILE_PATH = ROOT / "profile.json"

with PROFILE_PATH.open("r", encoding="utf-8") as f:
    PROFILE = json.load(f)

ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
USER_NAME = PROFILE["github"]["username"]
DOB = datetime.datetime.strptime(PROFILE["personal"]["age_dob"], "%Y-%m-%d")

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/vnd.github+json",
}

QUERY_COUNT = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "recursive_loc": 0,
    "loc_query": 0,
}

OWNER_ID = None


def daily_readme(birthday):
    """Return the user's integer age; it changes only on the birthday."""
    today = datetime.datetime.today()
    years = today.year - birthday.year - (
        (today.month, today.day) < (birthday.month, birthday.day)
    )
    return f"{years} years"


def simple_request(function_name, query, variables):
    request = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=60,
    )
    if request.status_code != 200:
        raise RuntimeError(
            f"{function_name} failed with HTTP {request.status_code}: "
            f"{request.text[:500]}"
        )
    payload = request.json()
    if payload.get("errors"):
        raise RuntimeError(f"{function_name} GraphQL error: {payload['errors']}")
    return payload


def query_count(function_id):
    QUERY_COUNT[function_id] += 1


def user_getter(username):
    query_count("user_getter")
    query = """
    query($login: String!) {
      user(login: $login) { id createdAt }
    }
    """
    data = simple_request(user_getter.__name__, query, {"login": username})
    user = data["data"]["user"]
    if not user:
        raise RuntimeError(f"GitHub user {username!r} was not found.")
    return {"id": user["id"]}


def follower_getter(username):
    query_count("follower_getter")
    query = """
    query($login: String!) {
      user(login: $login) { followers { totalCount } }
    }
    """
    data = simple_request(follower_getter.__name__, query, {"login": username})
    user = data["data"]["user"]
    if not user:
        raise RuntimeError(f"GitHub user {username!r} was not found.")
    return int(user["followers"]["totalCount"])


def graph_repos_stars(count_type, owner_affiliation):
    """Paginate repositories and return repository count or total stars."""
    query_count("graph_repos_stars")
    query = """
    query($login: String!, $affiliations: [RepositoryAffiliation], $cursor: String) {
      user(login: $login) {
        repositories(
          first: 100
          after: $cursor
          ownerAffiliations: $affiliations
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          totalCount
          edges { node { nameWithOwner stargazers { totalCount } } }
          pageInfo { endCursor hasNextPage }
        }
      }
    }
    """
    cursor = None
    total_stars = 0
    total_repos = 0
    while True:
        data = simple_request(
            graph_repos_stars.__name__,
            query,
            {"login": USER_NAME, "affiliations": owner_affiliation, "cursor": cursor},
        )
        user = data["data"]["user"]
        if not user:
            raise RuntimeError(f"GitHub user {USER_NAME!r} was not found.")
        repos = user["repositories"]
        total_repos = int(repos["totalCount"])
        for edge in repos["edges"]:
            total_stars += int(edge["node"]["stargazers"]["totalCount"])
        if not repos["pageInfo"]["hasNextPage"]:
            break
        cursor = repos["pageInfo"]["endCursor"]
        query_count("graph_repos_stars")
    return total_repos if count_type == "repos" else total_stars


def recursive_loc(owner, repo_name, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """Walk a repository's default-branch commit history with GraphQL cursors."""
    query_count("recursive_loc")
    query = """
    query($repo: String!, $owner: String!, $cursor: String) {
      repository(name: $repo, owner: $owner) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(first: 100, after: $cursor) {
                edges { node { author { user { id } } additions deletions } }
                pageInfo { endCursor hasNextPage }
              }
            }
          }
        }
      }
    }
    """
    data = simple_request(
        recursive_loc.__name__, query,
        {"repo": repo_name, "owner": owner, "cursor": cursor},
    )
    repository = data["data"]["repository"]
    if not repository or not repository["defaultBranchRef"]:
        return addition_total, deletion_total, my_commits
    target = repository["defaultBranchRef"]["target"]
    if not target:
        return addition_total, deletion_total, my_commits
    history = target["history"]
    for edge in history["edges"]:
        node = edge["node"]
        author = node.get("author") or {}
        user = author.get("user") or {}
        if user.get("id") == OWNER_ID:
            my_commits += 1
            addition_total += int(node.get("additions") or 0)
            deletion_total += int(node.get("deletions") or 0)
    if not history["pageInfo"]["hasNextPage"]:
        return addition_total, deletion_total, my_commits
    return recursive_loc(
        owner,
        repo_name,
        addition_total,
        deletion_total,
        my_commits,
        history["pageInfo"]["endCursor"],
    )


def cache_path():
    digest = hashlib.sha256(USER_NAME.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.txt"


def cache_builder(edges, comment_size=0, force_cache=False):
    """Cache per-repository LOC and invalidate entries when commit totals change."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = cache_path()
    if not filename.exists():
        with filename.open("w", encoding="utf-8") as f:
            f.write("# repo_hash commit_count my_commits additions deletions\n")

    raw_lines = filename.read_text(encoding="utf-8").splitlines(True)
    comments = raw_lines[:comment_size]
    old_entries = raw_lines[comment_size:]
    existing = {}
    for line in old_entries:
        parts = line.split()
        if len(parts) >= 5:
            existing[parts[0]] = parts

    changed = force_cache or len(existing) != len(edges)
    new_entries = []
    loc_add = loc_del = 0
    all_cached = True

    for edge in edges:
        node = edge["node"]
        full_name = node["nameWithOwner"]
        repo_hash = hashlib.sha256(full_name.encode("utf-8")).hexdigest()
        target = node.get("defaultBranchRef", {}).get("target") if node.get("defaultBranchRef") else None
        commit_count = int(target.get("history", {}).get("totalCount", 0)) if target else 0
        old = existing.get(repo_hash)
        needs_update = force_cache or changed or old is None or int(old[1]) != commit_count

        if needs_update:
            all_cached = False
            if commit_count:
                owner, repo_name = full_name.split("/", 1)
                adds, dels, mine = recursive_loc(owner, repo_name)
            else:
                adds = dels = mine = 0
            entry = [repo_hash, str(commit_count), str(mine), str(adds), str(dels)]
        else:
            entry = old

        new_entries.append(entry)
        loc_add += int(entry[3])
        loc_del += int(entry[4])

    with filename.open("w", encoding="utf-8") as f:
        f.writelines(comments)
        for entry in new_entries:
            f.write(" ".join(entry) + "\n")
    return [loc_add, loc_del, loc_add - loc_del, all_cached]


def loc_query(owner_affiliation, comment_size=0, force_cache=False):
    query_count("loc_query")
    query = """
    query($login: String!, $affiliations: [RepositoryAffiliation], $cursor: String) {
      user(login: $login) {
        repositories(
          first: 60
          after: $cursor
          ownerAffiliations: $affiliations
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          edges {
            node {
              nameWithOwner
              defaultBranchRef {
                target { ... on Commit { history { totalCount } } }
              }
            }
          }
          pageInfo { endCursor hasNextPage }
        }
      }
    }
    """
    edges = []
    cursor = None
    while True:
        data = simple_request(
            loc_query.__name__,
            query,
            {"login": USER_NAME, "affiliations": owner_affiliation, "cursor": cursor},
        )
        user = data["data"]["user"]
        if not user:
            raise RuntimeError(f"GitHub user {USER_NAME!r} was not found.")
        repos = user["repositories"]
        edges.extend(repos["edges"])
        if not repos["pageInfo"]["hasNextPage"]:
            break
        cursor = repos["pageInfo"]["endCursor"]
        query_count("loc_query")
    return cache_builder(edges, comment_size, force_cache)


def commit_counter(comment_size=0):
    filename = cache_path()
    if not filename.exists():
        return 0
    total = 0
    for line in filename.read_text(encoding="utf-8").splitlines()[comment_size:]:
        parts = line.split()
        if len(parts) >= 3:
            try:
                total += int(parts[2])
            except ValueError:
                continue
    return total


def add_archive():
    """Read historical deleted-repository data if present; empty archives are valid."""
    filename = CACHE_DIR / "repository_archive.txt"
    if not filename.exists():
        return [0, 0, 0, 0, 0]
    lines = filename.read_text(encoding="utf-8").splitlines()
    added = deleted = commits = contributed_repos = 0
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            added += int(parts[3])
            deleted += int(parts[4])
            commits += int(parts[2])
        except ValueError:
            continue
        contributed_repos += 1
    return [added, deleted, added - deleted, commits, contributed_repos]


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = str(new_text)


def justify_format(root, element_id, new_text, length=0):
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"
    new_text = str(new_text)
    if length:
        just_len = max(0, length - len(new_text))
        if just_len == 0:
            dots = ""
        elif just_len == 1:
            dots = " "
        elif just_len == 2:
            dots = ". "
        else:
            dots = " " + "." * just_len + " "
        find_and_replace(root, f"{element_id}_dots", dots)
    find_and_replace(root, element_id, new_text)


def apply_profile(root):
    p = PROFILE
    values = {
        "profile_title": f"{p['github']['username']}@{p['github']['hostname']}",
        "profile_os": p["personal"]["os"],
        "profile_status": p["personal"]["status"],
        "profile_university": p["personal"]["university"],
        "profile_location": p["personal"]["location"],
        "profile_focus": p["personal"]["focus"],
        "profile_education": p["education"]["degree"],
        "profile_cgpa": p["education"]["cgpa"],
        "profile_batch": p["education"]["batch"],
        "profile_passion": p["about"]["passionate_about"],
        "profile_learning": p["about"]["currently_learning"],
        "profile_interests": p["about"]["interests"],
        "profile_tech_stack": p["skills"]["tech_stack"],
        "profile_frameworks": p["skills"]["frameworks"],
        "profile_tools": p["skills"]["tools"],
        "profile_email": p["contact"]["email"],
        "profile_linkedin": p["contact"]["linkedin"],
        "profile_github": p["contact"]["github"],
        "profile_portfolio": p["contact"].get("portfolio", "Coming Soon"),
    }
    for element_id, value in values.items():
        find_and_replace(root, element_id, value)


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    tree = etree.parse(str(ROOT / filename))
    root = tree.getroot()
    apply_profile(root)
    justify_format(root, "age_data", age_data, 12)
    justify_format(root, "commit_data", commit_data, 8)
    justify_format(root, "star_data", star_data, 8)
    justify_format(root, "repo_data", repo_data, 6)
    justify_format(root, "contrib_data", contrib_data, 6)
    justify_format(root, "follower_data", follower_data, 6)
    justify_format(root, "loc_data", loc_data[2], 10)
    justify_format(root, "loc_add", loc_data[0], 10)
    justify_format(root, "loc_del", loc_data[1], 10)
    tree.write(str(ROOT / filename), encoding="utf-8", xml_declaration=True, pretty_print=True)


def perf_counter(function, *args):
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


if __name__ == "__main__":
    print("Calculation times:")
    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID = user_data["id"]
    age_data, age_time = perf_counter(daily_readme, DOB)
    loc_data, loc_time = perf_counter(loc_query, ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"], 0)
    commit_data, commit_time = perf_counter(commit_counter, 0)
    star_data, star_time = perf_counter(graph_repos_stars, "stars", ["OWNER"])
    repo_data, repo_time = perf_counter(graph_repos_stars, "repos", ["OWNER"])
    contrib_data, contrib_time = perf_counter(
        graph_repos_stars, "repos", ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"]
    )
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    archived = add_archive()
    loc_data[0] += archived[0]
    loc_data[1] += archived[1]
    loc_data[2] += archived[2]
    commit_data += archived[3]
    contrib_data += archived[4]

    for filename in ("dark_mode.svg", "light_mode.svg"):
        svg_overwrite(
            filename,
            age_data,
            commit_data,
            star_data,
            repo_data,
            contrib_data,
            follower_data,
            loc_data,
        )

    total_time = (
        user_time
        + age_time
        + loc_time
        + commit_time
        + star_time
        + repo_time
        + contrib_time
        + follower_time
    )
    print(f"Total function time: {total_time:.4f} s")
    print("GraphQL/API calls:", QUERY_COUNT)
