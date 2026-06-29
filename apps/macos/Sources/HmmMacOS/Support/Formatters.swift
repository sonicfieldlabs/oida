import Foundation

func shortLabel(_ value: String, maxLength: Int = 30) -> String {
    guard value.count > maxLength else { return value }
    let end = value.index(value.startIndex, offsetBy: max(0, maxLength - 3))
    return String(value[..<end]) + "..."
}

func displayStatus(_ status: String) -> String {
    status
        .split(separator: "_")
        .map { token in
            token.prefix(1).uppercased() + token.dropFirst()
        }
        .joined(separator: " ")
}

func compactTimestamp(_ isoString: String?) -> String {
    guard let isoString else { return "No timestamp" }
    return isoString
        .replacingOccurrences(of: "T", with: " ")
        .replacingOccurrences(of: "Z", with: "")
}
