import Foundation
import ServiceManagement

enum LaunchAtLoginManager {
    static var statusLabel: String {
        switch SMAppService.mainApp.status {
        case .enabled:
            return "Enabled"
        case .notRegistered:
            return "Disabled"
        case .requiresApproval:
            return "Requires approval"
        case .notFound:
            return "App not found"
        @unknown default:
            return "Unknown"
        }
    }

    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static func setEnabled(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}
