// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "OidaMacOS",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "oida-macos", targets: ["OidaMacOS"])
    ],
    targets: [
        .executableTarget(
            name: "OidaMacOS",
            path: "Sources/OidaMacOS"
        )
    ]
)
