#!/bin/bash

# File Master MCP Management Script
# This script provides commands to start, stop, restart, and check the status of the File Master MCP

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVER_SCRIPT="$SCRIPT_DIR/src/file_master_mcp_server.py"
PID_FILE="$SCRIPT_DIR/mcp_server.pid"
LOG_FILE="$SCRIPT_DIR/mcp_server.log"
SERVER_NAME="File Master MCP"
PORT=6466

# Function to check Python installation
check_python() {
    if command -v python3 &>/dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &>/dev/null; then
        PYTHON_CMD="python"
    else
        echo -e "${RED}Python not found. Please install Python 3.x${NC}"
        return 1
    fi
    return 0
}

# Function to setup virtual environment
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        echo -e "${YELLOW}Setting up virtual environment...${NC}"
        if ! check_python; then
            return 1
        fi
        
        $PYTHON_CMD -m venv "$VENV_DIR"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to create virtual environment${NC}"
            return 1
        fi
        
        # Activate and install requirements
        source "$VENV_DIR/bin/activate"
        pip install -r "$SCRIPT_DIR/requirements.txt"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to install requirements${NC}"
            return 1
        fi
        echo -e "${GREEN}Virtual environment setup complete${NC}"
    fi
    return 0
}

# Function to activate virtual environment
activate_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        if ! setup_venv; then
            return 1
        fi
    fi
    
    if [ -f "$VENV_DIR/bin/activate" ]; then
        source "$VENV_DIR/bin/activate"
    elif [ -f "$VENV_DIR/Scripts/activate" ]; then
        source "$VENV_DIR/Scripts/activate"
    else
        echo -e "${RED}Could not find virtual environment activation script${NC}"
        return 1
    fi
    return 0
}

# Function to check if server is running
check_server() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Function to check if port is in use
check_port() {
    if lsof -i :$PORT > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# Function to kill process using port
kill_port_process() {
    if check_port; then
        pid=$(lsof -ti :$PORT)
        if [ ! -z "$pid" ]; then
            echo -e "${YELLOW}Killing process $pid using port $PORT${NC}"
            kill -9 "$pid" 2>/dev/null
            sleep 1
            if check_port; then
                echo -e "${RED}Failed to kill process using port $PORT${NC}"
                return 1
            fi
        fi
    fi
    return 0
}

# Function to kill process and its children
kill_process_tree() {
    local pid=$1
    local child_pids=$(pgrep -P $pid 2>/dev/null)
    
    # Kill children first
    for child_pid in $child_pids; do
        kill_process_tree $child_pid
    done
    
    # Kill the parent process
    if ps -p $pid > /dev/null 2>&1; then
        kill -9 $pid 2>/dev/null
        sleep 1
    fi
}

# Function to stop the server
stop_server() {
    local force=$1
    local success=0

    # First try to stop using PID file
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}Stopping MCP server (PID: $pid)...${NC}"
            # Kill the process tree
            kill_process_tree $pid
            success=1
        fi
        rm -f "$PID_FILE"
    fi

    # If force stop or PID method failed, check port
    if [ "$force" = "force" ] || [ $success -eq 0 ]; then
        if check_port; then
            if kill_port_process; then
                success=1
            fi
        else
            success=1
        fi
    fi

    # Additional cleanup: Check for any Python processes running our script
    local script_name=$(basename "$SERVER_SCRIPT")
    local python_pids=$(ps aux | grep "$script_name" | grep -v grep | awk '{print $2}')
    for pid in $python_pids; do
        echo -e "${YELLOW}Cleaning up additional Python process (PID: $pid)...${NC}"
        kill_process_tree $pid
    done

    # Clean up any remaining venv Python processes
    if [ -d "$VENV_DIR" ]; then
        local venv_python_pids=$(ps aux | grep "$VENV_DIR/bin/python" | grep -v grep | awk '{print $2}')
        for pid in $venv_python_pids; do
            echo -e "${YELLOW}Cleaning up venv Python process (PID: $pid)...${NC}"
            kill_process_tree $pid
        done
    fi

    if [ $success -eq 1 ]; then
        echo -e "${GREEN}MCP server stopped${NC}"
    else
        echo -e "${RED}Failed to stop MCP server${NC}"
        return 1
    fi
}

# Function to start the server
start_server() {
    # Always try to stop any existing server first
    stop_server "force"
    
    # Check if port is free
    if check_port; then
        echo -e "${RED}Port $PORT is still in use. Cannot start server.${NC}"
        return 1
    fi

    # Setup and activate virtual environment
    if ! activate_venv; then
        echo -e "${RED}Failed to activate virtual environment${NC}"
        return 1
    fi

    echo -e "${GREEN}Starting MCP server...${NC}"
    
    # Start server using venv Python
    VENV_PYTHON="$VENV_DIR/bin/python"
    if [ ! -f "$VENV_PYTHON" ]; then
        VENV_PYTHON="$VENV_DIR/Scripts/python.exe"  # Windows path
    fi
    
    # Run the server directly without redirecting stdout/stderr
    # This is important for MCP protocol which requires direct stdout/stderr access
    "$VENV_PYTHON" "$SERVER_SCRIPT" &
    SERVER_PID=$!
    echo $SERVER_PID > "$PID_FILE"
    
    # Wait for server to start
    echo -e "${YELLOW}Waiting for server to start...${NC}"
    for i in {1..30}; do
        if check_port; then
            echo -e "${GREEN}MCP server started successfully (PID: $SERVER_PID)${NC}"
            echo -e "Log file: $LOG_FILE"
            return 0
        fi
        sleep 1
    done
    
    # If we get here, server didn't start
    echo -e "${RED}Failed to start MCP server. Check $LOG_FILE for details${NC}"
    rm -f "$PID_FILE"
    return 1
}

# Function to restart the server
restart_server() {
    stop_server "force"
    sleep 2
    start_server
}

# Function to show server status
show_status() {
    local server_running=0
    local port_in_use=0
    
    if check_server; then
        pid=$(cat "$PID_FILE")
        echo -e "${GREEN}MCP server process is running (PID: $pid)${NC}"
        server_running=1
    else
        echo -e "${RED}MCP server process is not running${NC}"
    fi
    
    if check_port; then
        pid=$(lsof -ti :$PORT)
        echo -e "${YELLOW}Port $PORT is in use by process $pid${NC}"
        port_in_use=1
    else
        echo -e "${GREEN}Port $PORT is free${NC}"
    fi
    
    if [ $server_running -eq 1 ] && [ $port_in_use -eq 1 ]; then
        echo -e "${GREEN}Server is fully operational${NC}"
    elif [ $server_running -eq 0 ] && [ $port_in_use -eq 1 ]; then
        echo -e "${RED}Warning: Port is in use but server process is not running${NC}"
    fi
    
    echo -e "Log file: $LOG_FILE"
    
    # Show last few lines of log file
    if [ -f "$LOG_FILE" ]; then
        echo -e "\nLast few lines of log file:"
        tail -n 5 "$LOG_FILE"
    fi
}

# Function to start the inspector
start_inspector() {
    # Stop any potentially running standalone server first to avoid port conflict
    echo -e "${YELLOW}Stopping any standalone server to avoid port conflict...${NC}"
    stop_server "force" > /dev/null 2>&1 # Suppress output unless error
    sleep 1

    # Setup and activate virtual environment
    if ! activate_venv; then
        echo -e "${RED}Failed to activate virtual environment${NC}"
        return 1
    fi

    echo -e "${GREEN}Starting MCP server and inspector using 'mcp dev'...${NC}"
    echo -e "${YELLOW}This command starts both the server and the inspector UI.${NC}"
    echo -e "${YELLOW}Look for the Inspector URL (usually http://127.0.0.1:6274) in the output below.${NC}"
    echo -e "${YELLOW}Press Ctrl+C in the terminal *where the inspector runs* to stop it (or use './manage_mcp.sh inspector stop').${NC}"

    # Run 'mcp dev' which starts the server script AND the inspector
    mcp dev "$SERVER_SCRIPT" &
    INSPECTOR_PID=$!
    # Note: This PID is for the 'mcp dev' process which manages both server and inspector
    echo $INSPECTOR_PID > "$SCRIPT_DIR/inspector.pid"
    echo -e "${GREEN}MCP Inspector process started (PID: $INSPECTOR_PID). Waiting for UI URL...${NC}"
}

# Function to stop the inspector
stop_inspector() {
    if [ -f "$SCRIPT_DIR/inspector.pid" ]; then
        pid=$(cat "$SCRIPT_DIR/inspector.pid")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}Stopping MCP inspector (PID: $pid)...${NC}"
            kill_process_tree $pid
            rm -f "$SCRIPT_DIR/inspector.pid"
            
            # Also kill any processes on inspector ports
            for port in 6274 6277; do
                if lsof -i :$port > /dev/null 2>&1; then
                    pid=$(lsof -ti :$port)
                    if [ ! -z "$pid" ]; then
                        echo -e "${YELLOW}Killing process $pid using port $port${NC}"
                        kill -9 "$pid" 2>/dev/null
                    fi
                fi
            done
            
            echo -e "${GREEN}MCP inspector stopped${NC}"
        else
            echo -e "${RED}MCP inspector process not found${NC}"
            rm -f "$SCRIPT_DIR/inspector.pid"
        fi
    else
        echo -e "${RED}MCP inspector PID file not found${NC}"
        # Try to kill any processes on inspector ports anyway
        for port in 6274 6277; do
            if lsof -i :$port > /dev/null 2>&1; then
                pid=$(lsof -ti :$port)
                if [ ! -z "$pid" ]; then
                    echo -e "${YELLOW}Killing process $pid using port $port${NC}"
                    kill -9 "$pid" 2>/dev/null
                fi
            fi
        done
    fi
}

# Main script
case "$1" in
    start)
        echo -e "${YELLOW}Starting server in standalone SSE mode (for Cursor, etc.)...${NC}"
        # Ensure inspector mode is not running
        stop_inspector > /dev/null 2>&1
        start_server
        ;;
    stop)
        echo -e "${YELLOW}Stopping standalone server (if running)...${NC}"
        stop_server "force"
        ;;
    restart)
        echo -e "${YELLOW}Restarting server in standalone SSE mode...${NC}"
        stop_inspector > /dev/null 2>&1
        restart_server
        ;;
    status)
        show_status
        ;;
    inspector)
        case "$2" in
            start)
                echo -e "${YELLOW}Starting server and inspector in development mode...${NC}"
                # Ensure standalone mode is not running
                stop_server "force" > /dev/null 2>&1
                start_inspector
                ;;
            stop)
                echo -e "${YELLOW}Stopping development mode (server and inspector)...${NC}"
                stop_inspector
                ;;
            *)
                echo "Usage: $0 inspector {start|stop}"
                exit 1
                ;;
        esac
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|inspector start|inspector stop}"
        echo "  start: Runs only the server (for clients like Cursor via SSE mcp.json)"
        echo "  stop: Stops only the standalone server"
        echo "  inspector start: Runs both server and inspector (for development/testing)"
        echo "  inspector stop: Stops the development server and inspector"
        exit 1
        ;;
esac

exit 0 