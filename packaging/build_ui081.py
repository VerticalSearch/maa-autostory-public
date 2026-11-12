"""0.8.1 visual redesign, preserving the released 0.8.0 backend bytecode."""
import build_ui_release as build

build.BASE = build.ROOT/'App-0.8.0'
build.TARGET = build.ROOT/'App-0.8.1'
build.VERSION_FILE = build.ROOT/'packaging/version-ui-081.txt'
build.BUILD_NAME = 'ui-release-081'
build.MANIFEST_NAME = 'package-ui-081-manifest.json'

if __name__ == '__main__':
    build.main()
