"""python-for-android build hook: inject the updater's FileProvider.

A ``<provider>`` element must live INSIDE ``<application>``, but buildozer
1.6 can only inject XML at ``<manifest>`` scope (android.extra_manifest_xml)
or attributes on the application tag — so this hook patches the dist's
manifest template directly. The ``@xml/file_paths`` resource it references
ships via ``android.add_resources`` (p4a resets src/main/res to a pristine
copy every build, so writing the file here would be wiped — the resources
channel is applied after that reset).

Wired via ``p4a.hook = ./p4a_hook.py`` in buildozer.spec.
"""
from pathlib import Path

_PROVIDER = """\
        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="${applicationId}.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>
"""

def before_apk_build(toolchain) -> None:
    dist_root = Path(toolchain.ctx.dist_dir)
    patched = skipped = 0
    for tmpl in dist_root.rglob("templates/AndroidManifest.tmpl.xml"):
        text = tmpl.read_text(encoding="utf-8")
        if "fileprovider" in text:
            skipped += 1  # already patched (idempotent across rebuilds)
            continue
        if "</application>" not in text:
            continue
        text = text.replace("</application>",
                            _PROVIDER + "    </application>")
        tmpl.write_text(text, encoding="utf-8")
        patched += 1
    print(f"[p4a_hook] FileProvider: {patched} manifest template(s) "
          f"patched, {skipped} already done, under {dist_root}")
