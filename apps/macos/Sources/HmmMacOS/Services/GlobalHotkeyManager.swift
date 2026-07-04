import Carbon
import Foundation

enum HotkeyRegistrationResult {
    case registered(String)
    case invalid(String)
    case failed(OSStatus)
}

/// Registers Carbon global hotkeys keyed by a small integer id, so the shell
/// can hold several bindings at once (toggle listener, listen now, …).
@MainActor
final class GlobalHotkeyManager {
    static let shared = GlobalHotkeyManager()

    private var handlerRef: EventHandlerRef?
    private var hotKeyRefs: [UInt32: EventHotKeyRef] = [:]
    private var actions: [UInt32: () -> Void] = [:]

    private init() {}

    func register(id: UInt32, bindingText: String, action: @escaping () -> Void) -> HotkeyRegistrationResult {
        unregister(id: id)
        guard let binding = HotkeyBinding.parse(bindingText) else {
            return .invalid("Use a modified key, e.g. control+option+h")
        }
        if let status = installHandlerIfNeeded(), status != noErr {
            return .failed(status)
        }

        var hotKeyRef: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: fourCharCode("HMMK"), id: id)
        let registerStatus = RegisterEventHotKey(
            binding.keyCode,
            binding.modifiers,
            hotKeyID,
            GetEventDispatcherTarget(),
            0,
            &hotKeyRef
        )
        guard registerStatus == noErr, let hotKeyRef else {
            return .failed(registerStatus)
        }
        hotKeyRefs[id] = hotKeyRef
        actions[id] = action
        return .registered(binding.display)
    }

    func unregister(id: UInt32) {
        if let ref = hotKeyRefs.removeValue(forKey: id) {
            UnregisterEventHotKey(ref)
        }
        actions.removeValue(forKey: id)
    }

    func unregisterAll() {
        for id in Array(hotKeyRefs.keys) {
            unregister(id: id)
        }
        if let handlerRef {
            RemoveEventHandler(handlerRef)
            self.handlerRef = nil
        }
    }

    fileprivate func fire(id: UInt32) {
        actions[id]?()
    }

    private func installHandlerIfNeeded() -> OSStatus? {
        guard handlerRef == nil else { return nil }
        var eventSpec = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        return InstallEventHandler(
            GetEventDispatcherTarget(),
            { _, event, userData in
                guard let userData, let event else { return noErr }
                var hotKeyID = EventHotKeyID()
                GetEventParameter(
                    event,
                    EventParamName(kEventParamDirectObject),
                    EventParamType(typeEventHotKeyID),
                    nil,
                    MemoryLayout<EventHotKeyID>.size,
                    nil,
                    &hotKeyID
                )
                let manager = Unmanaged<GlobalHotkeyManager>.fromOpaque(userData).takeUnretainedValue()
                let id = hotKeyID.id
                Task { @MainActor in
                    manager.fire(id: id)
                }
                return noErr
            },
            1,
            &eventSpec,
            Unmanaged.passUnretained(self).toOpaque(),
            &handlerRef
        )
    }
}

private struct HotkeyBinding {
    let modifiers: UInt32
    let keyCode: UInt32
    let display: String

    static func parse(_ text: String) -> HotkeyBinding? {
        let tokens = text
            .lowercased()
            .replacingOccurrences(of: " ", with: "")
            .split(separator: "+")
            .map(String.init)

        guard tokens.count >= 2 else { return nil }

        var modifiers: UInt32 = 0
        var key: String?

        for token in tokens {
            switch token {
            case "cmd", "command":
                modifiers |= UInt32(cmdKey)
            case "control", "ctrl":
                modifiers |= UInt32(controlKey)
            case "option", "opt", "alt":
                modifiers |= UInt32(optionKey)
            case "shift":
                modifiers |= UInt32(shiftKey)
            default:
                key = token
            }
        }

        guard modifiers != 0, let key, let keyCode = keyCodes[key] else {
            return nil
        }

        return HotkeyBinding(modifiers: modifiers, keyCode: keyCode, display: displayText(tokens))
    }

    private static func displayText(_ tokens: [String]) -> String {
        tokens
            .map { token in
                switch token {
                case "cmd", "command": return "Command"
                case "control", "ctrl": return "Control"
                case "option", "opt", "alt": return "Option"
                case "shift": return "Shift"
                case "space": return "Space"
                default: return token.uppercased()
                }
            }
            .joined(separator: "+")
    }
}

private let keyCodes: [String: UInt32] = [
    "a": UInt32(kVK_ANSI_A),
    "b": UInt32(kVK_ANSI_B),
    "c": UInt32(kVK_ANSI_C),
    "d": UInt32(kVK_ANSI_D),
    "e": UInt32(kVK_ANSI_E),
    "f": UInt32(kVK_ANSI_F),
    "g": UInt32(kVK_ANSI_G),
    "h": UInt32(kVK_ANSI_H),
    "i": UInt32(kVK_ANSI_I),
    "j": UInt32(kVK_ANSI_J),
    "k": UInt32(kVK_ANSI_K),
    "l": UInt32(kVK_ANSI_L),
    "m": UInt32(kVK_ANSI_M),
    "n": UInt32(kVK_ANSI_N),
    "o": UInt32(kVK_ANSI_O),
    "p": UInt32(kVK_ANSI_P),
    "q": UInt32(kVK_ANSI_Q),
    "r": UInt32(kVK_ANSI_R),
    "s": UInt32(kVK_ANSI_S),
    "t": UInt32(kVK_ANSI_T),
    "u": UInt32(kVK_ANSI_U),
    "v": UInt32(kVK_ANSI_V),
    "w": UInt32(kVK_ANSI_W),
    "x": UInt32(kVK_ANSI_X),
    "y": UInt32(kVK_ANSI_Y),
    "z": UInt32(kVK_ANSI_Z),
    "0": UInt32(kVK_ANSI_0),
    "1": UInt32(kVK_ANSI_1),
    "2": UInt32(kVK_ANSI_2),
    "3": UInt32(kVK_ANSI_3),
    "4": UInt32(kVK_ANSI_4),
    "5": UInt32(kVK_ANSI_5),
    "6": UInt32(kVK_ANSI_6),
    "7": UInt32(kVK_ANSI_7),
    "8": UInt32(kVK_ANSI_8),
    "9": UInt32(kVK_ANSI_9),
    "space": UInt32(kVK_Space),
    "escape": UInt32(kVK_Escape),
    "f1": UInt32(kVK_F1),
    "f2": UInt32(kVK_F2),
    "f3": UInt32(kVK_F3),
    "f4": UInt32(kVK_F4),
    "f5": UInt32(kVK_F5),
    "f6": UInt32(kVK_F6),
    "f7": UInt32(kVK_F7),
    "f8": UInt32(kVK_F8),
    "f9": UInt32(kVK_F9),
    "f10": UInt32(kVK_F10),
    "f11": UInt32(kVK_F11),
    "f12": UInt32(kVK_F12)
]

private func fourCharCode(_ value: String) -> OSType {
    var result: OSType = 0
    for scalar in value.unicodeScalars.prefix(4) {
        result = (result << 8) + OSType(scalar.value)
    }
    return result
}
