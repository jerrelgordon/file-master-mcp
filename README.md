# File Master MCP

A secure Model Context Protocol (MCP) server for managing files and directories. This server provides LLMs with controlled access to files through a standardized interface.

## Quick Start

1. **Install Python**
   - Make sure you have Python 3.7 or newer installed
   - You can download it from [python.org](https://python.org)

2. **Get the Code**
   ```bash
   # Clone the repository
   git clone https://github.com/jerrelgordon/file-master-mcp
   cd file-master-mcp
   ```

3. **Configure Your Directories**
   - Copy `config.json.template` to `config.json`:
   ```bash
   cp config.json.template config.json   # On macOS/Linux
   copy config.json.template config.json # On Windows
   ```
   - Then edit `config.json` and add your directories:
   ```json
   {
       "allowed_directories": [
           "/path/to/your/logs",
           "/another/path"
       ],
       "max_file_size_mb": 50,
       "supported_extensions": [
           ".log",
           ".txt"
       ],
       "server_startup_timeout_seconds": 15,
       "server_host": "127.0.0.1",
       "server_port": 6466,
       "allow_delete": false
   }
   ```
   The values shown above are the defaults from the template - adjust them according to your needs.

4. **Start the Server**
   ```bash
   # On Windows:
   file_master_mcp.bat start
   
   # On macOS/Linux:
   ./file_master_mcp.sh start
   ```
   The first time you run this, it will automatically:
   - Set up the Python environment
   - Install all dependencies
   - Create any missing configuration files
   - Start the server

That's it! Your server is now running and ready to use. 

To check if everything is working:
```bash
# On Windows:
file_master_mcp.bat status

# On macOS/Linux:
./file_master_mcp.sh status
```

## Features

- 🔒 Secure file system operations with strict permission controls
- 📁 Directory whitelisting and validation
- 📏 File size limits and extension filtering
- 📝 Comprehensive security logging
- 🔄 Real-time updates via Server-Sent Events (SSE)
- 🛠️ File and directory management (create, move, delete)
- 🔍 Powerful search and analysis capabilities
- 📊 Directory structure visualization

## Configuration Options

Edit `config.json` to customize your server. A template file `config.json.template` is provided with default values to get you started.

| Option | Description | Default |
|--------|-------------|---------|
| `allowed_directories` | List of directories the server can access | `[]` |
| `max_file_size_mb` | Maximum allowed file size | `50` |
| `supported_extensions` | Allowed file types | `[".log", ".txt"]` |
| `server_startup_timeout_seconds` | Server startup timeout | `15` |
| `server_host` | Server host address | `"127.0.0.1"` |
| `server_port` | Server port | `6466` |
| `allow_delete` | Enable file deletion | `false` |

## Server Management

Simple commands to control the server:

```bash
# On Windows:
file_master_mcp.bat start   # Start server
file_master_mcp.bat stop    # Stop server
file_master_mcp.bat restart # Restart server
file_master_mcp.bat status  # Check status

# On macOS/Linux:
./file_master_mcp.sh start   # Start server
./file_master_mcp.sh stop    # Stop server
./file_master_mcp.sh restart # Restart server
./file_master_mcp.sh status  # Check status
```

## Available Tools

The server provides these tools for file management:

### File Operations
- `get_files`: List files in a directory
- `get_directories`: Show directory structure
- `search_files`: Search file contents
- `get_files_content`: Analyze directory contents

### File Management
- `create_directory`: Make new directories
- `create_file`: Create new files
- `move_file`: Move files
- `move_directory`: Move directories
- `delete_file`: Remove files (if enabled)
- `delete_directory`: Remove directories (if enabled)

## Logging

The server creates two log files:
- `mcp_server.log`: General operations
- `security.log`: Security events

## Troubleshooting

1. **Server won't start**
   - Check if the port is already in use
   - Verify Python version (3.7+ required)
   - Look in `mcp_server.log` for errors

2. **Access denied errors**
   - Verify directories in `config.json`
   - Check file permissions
   - Ensure paths are absolute

3. **Can't delete files**
   - Check if `allow_delete` is `true` in `config.json`
   - Restart server after config changes

## Need Help?

- Check the logs in `mcp_server.log` and `security.log`
- Open an issue on GitHub

## Security Notes

- Keep `config.json` secure and regularly reviewed
- Use `allow_delete: false` unless needed
- Monitor `security.log` for unauthorized access attempts
- Use absolute paths in `allowed_directories`

## Requirements

- Python 3.7 or newer
- Operating System: Windows, macOS, or Linux
- Dependencies (installed automatically):
  - uvicorn
  - starlette
  - FastMCP

## License

File Master MCP is free software for personal and educational use:
- ✅ Free to use for personal and educational purposes
- ✅ Free to modify and share modifications
- ✅ Free to distribute for non-commercial purposes
- ❌ Commercial use requires permission

See the [LICENSE](LICENSE) file for the complete terms.
