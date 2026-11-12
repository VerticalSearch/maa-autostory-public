"""0.8.0 visual redesign, preserving the released 0.7.0 backend bytecode."""
import build_ui_release as build

build.BASE = build.ROOT/'App-0.7.0'
build.TARGET = build.ROOT/'App-0.8.0'
build.VERSION_FILE = build.ROOT/'packaging/version-ui-080.txt'
build.BUILD_NAME = 'ui-release-080'
build.MANIFEST_NAME = 'package-ui-080-manifest.json'

if __name__ == '__main__':
    build.main()
