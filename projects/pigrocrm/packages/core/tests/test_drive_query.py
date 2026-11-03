"""A Drive URL is born in one place only, and that place can only list children.

Spec 9C does not ask for a convention, it asks for a mechanism. Reading from Drive always
starts from a folder the owner named -- `google_drive_accounts.root_folder_ids` -- and
"list the children of this folder" is the only query the system needs to make. An open
search (`name contains '...'`) would cross the user's whole Drive, folders they never
handed over included, so it must not be *inconvenient*: it must be impossible to build.

As with Gmail (`test_gmail_query.py`, of which this file is the twin), there are two
guards, because each alone is one edit away from being bypassed:

1. `files_list_url` **refuses** a `q` that does not contain a well-formed `'<id>' in
   parents` clause, and refuses one that contains `contains`. The bug is not
   discouraged: it cannot be built.
2. `test_no_other_module_can_build_a_drive_url` walks the AST of every source file and
   fails if a literal contains the Drive host, the upload host or a `/files` path.
   Guard 1 is one line from being deleted; guard 2 is what makes that deletion
   insufficient, because whoever wants to bypass this module has to write a URL
   somewhere, and no place is left to write it.

The set of exceptions to guard 2 is **empty**: `storage/gdrive.py`, which used to write
Drive URLs by hand, was migrated to call this module (9C T1, ruling 4) instead of being
put on an exemption list.
"""

import ast
import inspect
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from pigrocrm.core.drive import query as module
from pigrocrm.core.drive.query import (
    DRIVE_API_ROOT,
    checked_outside_id,
    children_query,
    escape_query_value,
    file_export_url,
    file_media_url,
    file_meta_url,
    file_url,
    files_by_app_property_url,
    files_create_url,
    files_list_url,
    folder_by_name_query,
    upload_create_url,
    upload_media_url,
)
from pigrocrm.core.errors import ValidationFailed

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = (
    REPO_ROOT / "packages" / "core" / "src",
    REPO_ROOT / "apps" / "api" / "src",
    REPO_ROOT / "apps" / "mcp" / "src",
)
QUERY_MODULE = REPO_ROOT / "packages" / "core" / "src" / "pigrocrm" / "core" / "drive" / "query.py"
# Any one of these in a literal means someone is assembling a Drive URL by hand.
# `/files` also catches `f"{DRIVE_API_ROOT}/files?q=..."`, which would slip past a scan
# of the host alone; `googleapis.com/upload` catches the upload endpoint, which lives on
# a different path of the same host.
URL_FRAGMENTS = ("googleapis.com/drive", "googleapis.com/upload", "/files")

# Empty, and it must stay so. `storage/gdrive.py` was the only candidate for an exception
# and it has been migrated (ruling 4 of 9C T1): declaring an exemption for the only module
# that touches Drive today would have made the guard a formality in exactly the file
# where someone will add the next call. The pairs are exact `(file, literal)`, not whole
# files, so a *second* literal in the same module fails anyway; and they are re-checked
# below, so a stale entry cannot silently outlive what it described.
ALLOWED_LITERALS: frozenset[tuple[Path, str]] = frozenset()

FOLDER = "1AbCdEfGhIjKlMnOpQ"


# --- the only query needed: the children of a folder --------------------------------


def test_children_query_names_one_folder_and_excludes_the_trash() -> None:
    assert children_query(FOLDER) == f"'{FOLDER}' in parents and trashed = false"


def test_a_folder_id_that_could_break_out_of_the_query_is_refused() -> None:
    """The id comes from `google_drive_accounts.root_folder_ids`, that is, ultimately from
    something the owner pasted: a quote would close the clause and the rest would become
    a search expression chosen by whoever pasted it."""
    hostile = [
        "1AbCdEfGhIjKlMnOpQ' or name contains 'fattura",
        "1AbCdEfGhIjKlMnOpQ or trashed = true",
        "1AbCdEfGhIjKlMnOpQ ",
        "1AbCd/EfGhIjKlMnOpQ",
        "../../drives",
        "",
        # Too short to be a Drive id: real ids run between 28 and 44 characters, and
        # accepting one of three means accepting any word.
        "abc",
        "x" * 129,
    ]
    for folder_id in hostile:
        with pytest.raises(ValidationFailed) as caught:
            children_query(folder_id)
        assert caught.value.details["field"] == "folder_id"


def test_checked_outside_id_names_the_field_it_was_given() -> None:
    """The same check, two names. An id that arrives from outside can be the root folder
    pasted by the owner or the file an agent asked to read, and the required shape is
    identical -- but the message is not: whoever reads «not a Drive folder id» after
    passing the id of a *file* goes looking for the error in the roots configuration
    instead of in the parameter they wrote themselves.
    """
    assert checked_outside_id(FOLDER, field="folder_id") == FOLDER
    assert checked_outside_id(FOLDER, field="file_id") == FOLDER

    with pytest.raises(ValidationFailed) as as_file:
        checked_outside_id("abc", field="file_id")
    assert as_file.value.details["field"] == "file_id"
    assert as_file.value.details["entity"] == "drive_query"

    with pytest.raises(ValidationFailed) as as_folder:
        checked_outside_id("abc", field="folder_id")
    assert as_folder.value.details["field"] == "folder_id"
    # One sentence for both, naming neither kind: it is the shape of the id that is wrong,
    # not what is on the other side.
    assert as_file.value.details["reason"] == as_folder.value.details["reason"]
    assert "cartella" not in as_file.value.details["reason"]


def test_checked_outside_id_is_the_check_children_query_already_did() -> None:
    """The same severity as before, not a new one: the ids `children_query` refused stay
    refused, with the same `field`."""
    for hostile in ("", "abc", "1AbCd/EfGhIjKlMnOpQ", "x" * 129, "1AbCdEfGhIjKlMnOpQ "):
        with pytest.raises(ValidationFailed) as caught:
            checked_outside_id(hostile, field="folder_id")
        assert caught.value.details["field"] == "folder_id"
        with pytest.raises(ValidationFailed):
            children_query(hostile)


# --- guard one: the list URL refuses anything that is not a list of children --------


def test_the_list_url_refuses_a_query_that_is_not_scoped_to_a_folder() -> None:
    """Guard one. The only function that can build a `files.list` builds none that is not
    limited to a given folder: so "read the whole Drive" is not discouraged, it is
    unbuildable."""
    unscoped = [
        "",
        "   ",
        "trashed = false",
        "mimeType='application/pdf'",
        "name='fattura.pdf'",
        # The shape of the id matters, not the presence of the words: `in parents` with no
        # well-formed clause in front of it limits nothing.
        "in parents",
        "'' in parents",
        "'1AbCd EfGhIjKlMnOpQ' in parents",
    ]
    for bad in unscoped:
        with pytest.raises(ValidationFailed, match="cartella"):
            files_list_url(bad, fields="files(id)")


def test_the_list_url_refuses_a_full_text_search_even_inside_a_folder() -> None:
    """`contains` is Drive's free-search operator. Even restricted to a folder it is not
    what the import does -- the import lists and decides afterwards -- and admitting it
    here would mean admitting that a search string can arrive from outside."""
    for bad in [
        f"'{FOLDER}' in parents and name contains 'fattura'",
        f"'{FOLDER}' in parents and fullText contains 'fattura'",
        f"'{FOLDER}' in parents and name CONTAINS 'fattura'",
    ]:
        with pytest.raises(ValidationFailed, match="contains"):
            files_list_url(bad, fields="files(id)")


def test_the_list_url_refuses_a_query_that_widens_past_its_folder() -> None:
    """That an `in parents` clause is there is not enough: it must be the only thing that
    decides *where* to look. `'<id>' in parents or mimeType='application/pdf'` contains
    the clause and still lists every PDF in the Drive, so a presence-only check would
    make this module's docstring false. A list can only narrow: `or`, `not` and
    parentheses -- which exist to group an `or` -- are the three ways to widen, and all
    three are refused."""
    for bad in [
        children_query(FOLDER) + " or mimeType='application/pdf'",
        f"not {children_query(FOLDER)}",
        f"'{FOLDER}' in parents and not mimeType='application/pdf'",
        f"'{FOLDER}' in parents OR '{FOLDER}' in parents",
        f"('{FOLDER}' in parents)",
        f"'{FOLDER}' in parents and (trashed = false or trashed = true)",
    ]:
        with pytest.raises(ValidationFailed, match="allarga"):
            files_list_url(bad, fields="files(id)")


def test_a_value_in_quotes_is_never_mistaken_for_an_operator() -> None:
    """The checks run on a skeleton in which every quoted literal has been emptied, not on
    the raw `q`. This is not a refinement: a customer's legal name becomes a storage key
    segment, hence a folder name, hence a value inside `name='...'` -- and "Contains
    S.r.l." would have made *every* put and get of that customer fail with a
    `ValidationFailed` on a `q` the CRM had built itself. The refusal applies to the
    operator, not to the word."""
    assert files_list_url(folder_by_name_query("id2", "contains-srl-01234567"), fields="files(id)")
    assert files_list_url(folder_by_name_query("id2", "or-not-spa"), fields="files(id)")
    # Quote included: the skeleton must also skip an escaped quote, otherwise it would
    # lose count of the quotes on exactly the most hostile value.
    assert files_list_url(
        folder_by_name_query("id2", "bar's or fullText contains x"), fields="files(id)"
    )
    # And outside the quotes the operator stays forbidden.
    with pytest.raises(ValidationFailed, match="contains"):
        files_list_url(f"'{FOLDER}' in parents and name contains 'x'", fields="files(id)")


def test_the_list_url_accepts_a_query_that_is_scoped_and_says_where_it_is_going() -> None:
    url = files_list_url(children_query(FOLDER), page_token="tok", fields="files(id,name)")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.googleapis.com"
    assert parsed.path == "/drive/v3/files"
    assert query["q"] == [f"'{FOLDER}' in parents and trashed = false"]
    assert query["pageToken"] == ["tok"]
    assert query["fields"] == ["files(id,name)"]
    # An owner's folder lives on a shared Drive as much as a service account's does:
    # without these two parameters Drive answers 404 to both.
    assert query["supportsAllDrives"] == ["true"]
    assert query["includeItemsFromAllDrives"] == ["true"]


def test_the_first_page_has_no_page_token_at_all() -> None:
    assert "pageToken" not in files_list_url(children_query(FOLDER), fields="files(id)")


def test_every_query_this_module_builds_is_one_it_would_also_accept() -> None:
    """The two guards must agree. A builder that produced a `q` the URL builder then
    refuses would fail at the first import, not here."""
    assert files_list_url(children_query(FOLDER), fields="files(id)")
    assert files_list_url(folder_by_name_query(FOLDER, "acme-01234567"), fields="files(id)")
    # Also with a short id: the ones `_ensure_folder` passes around come from Drive itself,
    # not from a paste, and are opaque by contract.
    assert files_list_url(folder_by_name_query("id2", "acme-01234567"), fields="files(id)")


# --- the folder by name: placement, and the only query with a value inside ----------


def test_folder_by_name_query_is_scoped_named_and_typed() -> None:
    assert folder_by_name_query("id2", "acme-01234567") == (
        "trashed=false and name='acme-01234567' and 'id2' in parents "
        "and mimeType='application/vnd.google-apps.folder'"
    )


def test_a_folder_name_is_escaped_rather_than_refused() -> None:
    """The name is a storage key segment generated by the CRM, not user text: its
    character class today contains neither a quote nor a backslash. The escaping stays
    because a future widening of that class must not silently reopen the injection just
    because nobody exercises it today."""
    assert "name='bar\\'s'" in folder_by_name_query("id2", "bar's")


def test_escaping_handles_the_backslash_before_the_quote() -> None:
    """Reversing the order would leave a lone backslash able to eat the filter's closing
    quote: the same reason the template escapers do it in this order."""
    assert escape_query_value("bar's") == "bar\\'s"
    assert escape_query_value("a\\b") == "a\\\\b"
    assert escape_query_value("a\\'b") == "a\\\\\\'b"


# --- identity: appProperties, the declared exception to guard one -------------------


def test_the_app_property_url_asks_for_one_exact_key_across_every_drive() -> None:
    """The only query not limited to a folder, and it is so because it is *more*
    specific, not less: it names a value the CRM itself wrote into `appProperties`, not
    a Drive search. Same logic as Gmail's `rfc822msgid` exception. The shape of the
    clause is exact because it is the one `FakeDrive._list` can read, as well as the
    one Drive accepts."""
    url = files_by_app_property_url(
        "pigrocrm_key", "acme-01234567/0199abcd/v1.pdf", fields="files(id)"
    )
    query = parse_qs(urlparse(url).query)
    assert query["q"] == [
        "trashed=false and appProperties has "
        "{ key='pigrocrm_key' and value='acme-01234567/0199abcd/v1.pdf' }"
    ]
    assert query["corpora"] == ["allDrives"]
    assert query["includeItemsFromAllDrives"] == ["true"]
    assert query["supportsAllDrives"] == ["true"]


def test_the_app_property_value_is_escaped_not_interpolated_raw() -> None:
    url = files_by_app_property_url("pigrocrm_key", "bar's", fields="files(id)")
    assert "value='bar\\'s'" in parse_qs(urlparse(url).query)["q"][0]


def test_an_app_property_key_that_is_not_a_plain_name_is_refused() -> None:
    """The key is a constant of the code, not an input: checking it costs one line and
    makes it impossible for it to become an input by distraction."""
    for hostile in ["pigrocrm_key' and name contains 'x", "", "key with spaces"]:
        with pytest.raises(ValidationFailed) as caught:
            files_by_app_property_url(hostile, "v1.pdf", fields="files(id)")
        assert caught.value.details["field"] == "key"


def test_the_strict_list_url_would_have_refused_the_app_property_query() -> None:
    """Why a second builder exists instead of a `strict=False` parameter: the exemption
    is another function, with another name, that says in its name what it filters."""
    with pytest.raises(ValidationFailed):
        files_list_url(
            "trashed=false and appProperties has { key='pigrocrm_key' and value='v1.pdf' }",
            fields="files(id)",
        )


# --- the URLs for a single file ------------------------------------------------------


def test_the_single_file_urls_carry_the_id_in_the_path_and_all_drives_support() -> None:
    assert file_url("id2") == f"{DRIVE_API_ROOT}/files/id2?supportsAllDrives=true"
    assert file_media_url("id2") == f"{DRIVE_API_ROOT}/files/id2?supportsAllDrives=true&alt=media"
    meta = parse_qs(urlparse(file_meta_url("id2", fields="id,driveId")).query)
    assert meta["fields"] == ["id,driveId"]
    assert meta["supportsAllDrives"] == ["true"]
    assert meta["includeItemsFromAllDrives"] == ["true"]


def test_the_export_url_asks_for_a_conversion_and_defaults_to_plain_text() -> None:
    """`files.export` is the only endpoint here without `supportsAllDrives`: it is not one
    of its parameters (Google's generated client does not expose it), and passing one
    the API does not document is a 400 waiting to happen. A Google Doc on a Shared Drive
    exports anyway, because access is by fileId."""
    url = file_export_url("id2")
    assert urlparse(url).path == "/drive/v3/files/id2/export"
    assert parse_qs(urlparse(url).query) == {"mimeType": ["text/plain"]}
    assert parse_qs(urlparse(file_export_url("id2", "text/csv")).query)["mimeType"] == ["text/csv"]


def test_an_export_mime_type_that_is_not_one_is_refused() -> None:
    for hostile in ["text/plain&alt=media", "not a mime", "", "text/plain "]:
        with pytest.raises(ValidationFailed) as caught:
            file_export_url("id2", hostile)
        assert caught.value.details["field"] == "mime"


def test_a_file_id_that_is_not_one_is_refused_by_every_path_builder() -> None:
    """These ids come from Drive's own listing, so a hostile one means the response was
    tampered with -- but path traversal to another endpoint costs one line to make
    impossible and a lot to discover afterwards."""
    for hostile in ["../../drives/other", "id 2", "id/2", "", "x" * 129]:
        for builder in (file_url, file_media_url, file_export_url, upload_media_url):
            with pytest.raises(ValidationFailed):
                builder(hostile)
        with pytest.raises(ValidationFailed):
            file_meta_url(hostile, fields="id")


# --- writing: create and upload ------------------------------------------------------


def test_the_create_and_upload_urls_say_which_upload_they_are() -> None:
    assert files_create_url(fields="id") == (
        f"{DRIVE_API_ROOT}/files?supportsAllDrives=true&fields=id"
    )
    multipart = parse_qs(urlparse(upload_create_url(fields="id")).query)
    assert multipart["uploadType"] == ["multipart"]
    assert multipart["supportsAllDrives"] == ["true"]
    assert urlparse(upload_create_url(fields="id")).path == "/upload/drive/v3/files"
    media = urlparse(upload_media_url("id2"))
    assert media.path == "/upload/drive/v3/files/id2"
    assert parse_qs(media.query)["uploadType"] == ["media"]


def test_no_helper_takes_a_caller_supplied_search_string() -> None:
    """No surface of this slice accepts a Drive search string. The module exposes exactly
    these builders, and the only one that takes a `q` -- `files_list_url` -- refuses
    everything this module did not build itself. Listing the names here makes adding a
    `search_url(text)` a change someone must *decide* to make, not one that slips through
    a diff."""
    exported = sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        # Defined here, not merely imported here: `urlencode` is a public function in this
        # module's namespace and has nothing to do with the surface being pinned.
        and value.__module__ == module.__name__
    )
    assert exported == [
        "checked_outside_id",
        "children_query",
        "escape_query_value",
        "file_export_url",
        "file_media_url",
        "file_meta_url",
        "file_url",
        "files_by_app_property_url",
        "files_create_url",
        "files_list_url",
        "folder_by_name_query",
        "upload_create_url",
        "upload_media_url",
    ]


# --- guard two: no other module may write one of these URLs -------------------------


def _string_constants(path: Path) -> list[str]:
    """Only the string literals, from the AST.

    Not a `grep`: this file, the module docstring and half the slice's comments talk
    about `/files` in prose, and a scan unable to tell a sentence from a URL would be
    either noisy enough to be switched off or narrow enough to be useless.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_other_module_can_build_a_drive_url() -> None:
    """Guard two. That `files_list_url` refuses a `q` not limited to a folder is worth
    exactly as much as the guarantee that it is the only way to reach `files.list`:
    otherwise the next caller writes their own f-string and the refusal protects a
    function nobody calls.

    Docstrings are included by the very shape of the AST walk: they are `ast.Constant`
    too, so a module that merely *documented* the endpoint would fail. That is deliberate
    and cheap to satisfy -- write `files.list` instead of the path, or name the helper --
    and it is the price of not having to tell a URL from a sentence that mentions one.
    """
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        # A scan that looks at no file always passes. If a package ever moves, this test
        # must fail for that, not turn green on an empty set.
        assert list(root.rglob("*.py")), root
        for path in sorted(root.rglob("*.py")):
            if path == QUERY_MODULE:
                continue
            for literal in _string_constants(path):
                if (path, literal) in ALLOWED_LITERALS:
                    continue
                for fragment in URL_FRAGMENTS:
                    if fragment in literal:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {literal!r}")
    assert offenders == [], (
        "only drive/query.py may build a Drive URL: a listing built elsewhere escapes "
        "the constraint of staying inside a folder the owner named (spec 9C). Use "
        "children_query / files_list_url / file_meta_url / "
        "file_media_url / file_export_url / files_by_app_property_url / "
        "files_create_url / upload_create_url / upload_media_url.\n" + "\n".join(offenders)
    )


def test_every_allowed_literal_is_still_present_in_that_file() -> None:
    """The guard of the exemption list. An entry that outlived what it described would
    widen the scan's blind spot by exactly one literal, in the file where someone is most
    likely to add a Drive call. Today the list is empty and this test has nothing to do:
    it is here so that the day someone adds an exemption, the exemption is born with its
    own expiry check."""
    for path, literal in ALLOWED_LITERALS:
        assert path.exists(), f"{path} no longer exists: the exception must be removed"
        assert literal in _string_constants(path), (
            f"{path.relative_to(REPO_ROOT)} no longer contains {literal!r}: "
            "the exception must be removed together with the code that justified it"
        )


def test_the_guard_above_would_notice_a_hand_built_drive_url(tmp_path: Path) -> None:
    """A scan that cannot fail is a scan nobody will notice has broken. This one runs the
    same reader over a file that does exactly what the guard forbids."""
    offender = tmp_path / "importer.py"
    offender.write_text(
        "URL = \"https://www.googleapis.com/drive/v3/files?q=name contains 'fattura'\"\n"
        'UP = "https://www.googleapis.com/upload/drive/v3/files?uploadType=media"\n',
        encoding="utf-8",
    )
    literals = _string_constants(offender)
    assert sum(fragment in literal for literal in literals for fragment in URL_FRAGMENTS) == 4, (
        literals
    )
