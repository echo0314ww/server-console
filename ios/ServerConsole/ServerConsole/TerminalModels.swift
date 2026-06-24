import Foundation

struct ServerMessage: Decodable {
    let type: String
    let data: String?
    let message: String?
    let shell: String?
    let cols: Int?
    let rows: Int?
    let code: Int?
    let time: Double?
}

struct TerminalSize: Equatable {
    var cols: Int
    var rows: Int
}

struct ClientMessage: Encodable {
    let type: String
    let data: String?
    let cols: Int?
    let rows: Int?

    static func input(_ text: String) -> ClientMessage {
        let normalized = text.hasSuffix("\n") ? text : text + "\n"
        return ClientMessage(
            type: "input",
            data: Data(normalized.utf8).base64EncodedString(),
            cols: nil,
            rows: nil
        )
    }

    static func rawInput(_ text: String) -> ClientMessage {
        ClientMessage(
            type: "input",
            data: Data(text.utf8).base64EncodedString(),
            cols: nil,
            rows: nil
        )
    }

    static func control(_ bytes: [UInt8]) -> ClientMessage {
        ClientMessage(
            type: "input",
            data: Data(bytes).base64EncodedString(),
            cols: nil,
            rows: nil
        )
    }

    static func resize(cols: Int, rows: Int) -> ClientMessage {
        ClientMessage(type: "resize", data: nil, cols: cols, rows: rows)
    }

    static var ping: ClientMessage {
        ClientMessage(type: "ping", data: nil, cols: nil, rows: nil)
    }

    static var kill: ClientMessage {
        ClientMessage(type: "kill", data: nil, cols: nil, rows: nil)
    }

    static var restart: ClientMessage {
        ClientMessage(type: "restart", data: nil, cols: nil, rows: nil)
    }
}

enum ConnectionState: Equatable {
    case disconnected
    case connecting
    case connected(shell: String?)
    case failed(String)

    var title: String {
        switch self {
        case .disconnected:
            return "Disconnected"
        case .connecting:
            return "Connecting"
        case .connected(let shell):
            return shell ?? "Connected"
        case .failed:
            return "Failed"
        }
    }

    var isConnected: Bool {
        if case .connected = self {
            return true
        }
        return false
    }
}
