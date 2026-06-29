// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "HmmMacOS",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "hmm-macos", targets: ["HmmMacOS"])
    ],
    targets: [
        .executableTarget(
            name: "HmmMacOS",
            path: "Sources/HmmMacOS"
        )
    ]
)
