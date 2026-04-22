# -*- coding: utf-8 -*-

# Python script to extract the keys from a Bambu Lab filament RFID tag, using a Proxmark3
# Created for https://github.com/Bambu-Research-Group/RFID-Tag-Guide

import os
import re
import sys
from pathlib import Path

from lib import strip_color_codes, get_proxmark3_location, run_command

dictionaryFilename = "myKeyDictionary.dic"
dictionaryFilepath = ""

pm3Location = None
pm3Command = "bin/pm3"
# mf_nonce_brute lives under share/ on Unix; on Windows it is a .exe in the client bin
mfNonceBruteCommand = (
    "client/mf_nonce_brute"
    if os.name == "nt"
    else "share/proxmark3/tools/mf_nonce_brute"
)

def setup():
    global pm3Location, dictionaryFilepath

    pm3Location = get_proxmark3_location()
    if not pm3Location:
        exit(-1)

    print(f"Creating dictionary file '{dictionaryFilename}'")
    open(dictionaryFilename, "w").close()
    dictionaryFilepath = os.path.abspath(dictionaryFilename)
    print(f"Saved dictionary to {dictionaryFilepath}")

def main():
    print("--------------------------------------------------------")
    print("RFID Key Extractor v0.2.1 - Bambu Research Group 2024")
    print("--------------------------------------------------------")
    print("This will extract the keys from a trace file")
    print("that was saved from sniffing communication between")
    print("the AMS and RFID tag.")
    print("")
    print("Instructions to sniff and save the trace can be found at")
    print("https://github.com/Bambu-Research-Group/RFID-Tag-Guide")
    print("--------------------------------------------------------")
    print("")

    setup()

    if len(sys.argv) > 1:
        trace = os.path.abspath(sys.argv[1])
    else:
        print()
        print("Start by creating a trace file. In the proxmark terminal, execute command `hf 14a sniff -c -r`.")
        print("Then, place the Proxmark3 between the RFID reader and spool.")
        print("Load in filament and wait for the process to complete, then press the button on the Proxmark3.")
        print("Finally, in the proxmark terminal, execute command `trace save -f [FILEPATH]` to create the trace file.")
        print("See the GitHub repository for more details.")
        print()
        trace = input("Enter trace name or full trace filepath: ")

    discoverKeys(trace)

    print("Keys obtained. Remove the spool from the AMS and place the Proxmark3 on the spool's tag.")
    print(f"In proxmark terminal, execute command `hf mf fchk -f {dictionaryFilepath} --dump` to create a keyfile from this dictionary.")
    print("Then, execute `hf mf dump` to dump the contents of the RFID tag.")


def discoverKeys(traceFilepath):
    keyList = []

    for i in range(16):
        print("----------------------")
        print(f"Loop {i+1} of 16")
        print(f"Viewing tracelog with {len(keyList)} discovered keys")

        output = run_command([pm3Location / pm3Command, "-o", "-c",
                              f"trace load -f {traceFilepath}; trace list -1 -t mf -f {dictionaryFilepath}"])

        if output is None:
            print("Warning: no output from proxmark3, skipping loop")
            continue

        for line in output.splitlines():
            if " key " in line or " key: " in line:
                line = ' '.join(line.split())
                print()
                print("Found line containing a key:")
                print(f"    {line}")
                words = line.split(" ")

                key = ""
                for j in range(len(words) - 1):
                    if words[j] in ("key", "key:"):
                        key = words[j + 1]
                        break

                if not key:
                    continue

                key = key.replace('|', '').upper()

                if key in keyList:
                    print(f"    Duplicate key, ignoring: {key}")
                    continue

                keyList.append(key)
                print(f"    Found new key: {key}")

            if "mf_nonce_brute" in line:
                print()
                print("Found line requiring decoding:")
                print(line)

                args = []
                words = line.split(" ")
                for j in range(len(words) - 1):
                    if "mf_nonce_brute" in words[j]:
                        args = words[j + 1:]
                        break

                key = bruteForce(args)
                if not key:
                    continue

                key = strip_color_codes(key).upper()

                if key in keyList:
                    print(f"    Duplicate key, ignoring: {key}")
                    continue

                keyList.append(key)
                print(f"    Found new key: {key}")

        with open(dictionaryFilename, "w") as f:
            print()
            print("Found keys:")
            for j, k in enumerate(keyList):
                print(f"    {j}: {k}")
                f.write(k + "\n")
            print()

    print(f"{len(keyList)} keys saved to file: {dictionaryFilepath}")


def bruteForce(args):
    print("Running bruteforce command:")
    output = run_command([pm3Location / mfNonceBruteCommand] + args)

    if output is None:
        print("Warning: bruteforce command returned no output")
        return ""

    for line in output.splitlines():
        if "Valid Key" in line and "matches candidate" in line:
            print(f"    {line}")
            words = line.split(" ")
            for i in range(len(words) - 1):
                if words[i] == "[":
                    return words[i + 1]

    return ""


if __name__ == "__main__":
    main()
