import Foundation

enum ListeningPhase: String, Codable {
    case idle
    case capturing
    case processing
    case result
    case failed
}

struct DashboardNativeState: Codable, Equatable {
    let phase: String
    let source: String
    let preset: String
    let direction: String
    let captureSeconds: Double
    let progress: Double
    let secondsRemaining: Double?
    let status: String
    let error: String?
    let eventId: String?
    let logs: [String]
    let customMode: Bool
    let selectedSkillIDs: [String]?
    let musicIDEnabled: Bool
    let appearance: String
}

struct DashboardShellMessage {
    let action: String
    let source: String?
    let preset: String?
    let direction: String?
    let seconds: Double?
    let customMode: Bool?
    let selectedSkillIDs: [String]?
    let musicIDEnabled: Bool?
    let appearance: String?
    let sessionName: String?

    init?(body: Any) {
        if let action = body as? String {
            self.action = action
            source = nil
            preset = nil
            direction = nil
            seconds = nil
            customMode = nil
            selectedSkillIDs = nil
            musicIDEnabled = nil
            appearance = nil
            sessionName = nil
            return
        }
        guard let payload = body as? [String: Any], let action = payload["action"] as? String else {
            return nil
        }
        self.action = action
        source = payload["source"] as? String
        preset = payload["preset"] as? String
        direction = payload["direction"] as? String
        customMode = (payload["custom"] as? NSNumber)?.boolValue
        selectedSkillIDs = payload["skills"] as? [String]
        musicIDEnabled = (payload["musicId"] as? NSNumber)?.boolValue
        appearance = payload["appearance"] as? String
        sessionName = payload["sessionName"] as? String
        if let value = payload["seconds"] as? NSNumber {
            seconds = value.doubleValue
        } else {
            seconds = nil
        }
    }
}
