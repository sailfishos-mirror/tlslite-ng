#!/usr/bin/env python

# Author: Trevor Perrin
# See the LICENSE file for legal information regarding use of this file.

import sys
import os
import os.path
import socket
import thread
import time
import getopt
import httplib
from SocketServer import *
from BaseHTTPServer import *
from SimpleHTTPServer import *

if __name__ != "__main__":
    raise "This must be run as a command, not used as a module!"

from tlslite.api import *
from tlslite import __version__

try:
    from TACKpy import TACK, TACK_Break_Sig, writeTextTACKStructures
except ImportError:
    pass

def printUsage(s=None):
    if m2cryptoLoaded:
        crypto = "M2Crypto/OpenSSL"
    else:
        crypto = "Python crypto"        
    if s:
        print("ERROR: %s" % s)
    print("""\ntls.py version %s (using %s)  

Commands:
  server  
    [-k KEY] [-c CERT] [-t TACK] [-b BREAKSIGS] [-v VERIFIERDB] [-d DIR]
    [--reqcert] HOST:PORT

  client
    [-k KEY] [-c CERT] [-u USER] [-p PASS]
    HOST:PORT
""" % (__version__, crypto))
    sys.exit(-1)

def printError(s):
    """Print error message and exit"""
    sys.stderr.write("ERROR: %s\n" % s)
    sys.exit(-1)


def handleArgs(argv, argString, flagsList=[]):
    # Convert to getopt argstring format:
    # Add ":" after each arg, ie "abc" -> "a:b:c:"
    getOptArgString = ":".join(argString) + ":"
    try:
        opts, argv = getopt.getopt(argv, getOptArgString, flagsList)
    except getopt.GetoptError as e:
        printError(e) 
    # Default values if arg not present  
    privateKey = None
    certChain = None
    username = None
    password = None
    tack = None
    breakSigs = None
    verifierDB = None
    reqCert = False
    directory = None
    
    for opt, arg in opts:
        if opt == "-k":
            s = open(arg, "rb").read()
            privateKey = parsePEMKey(s, private=True)            
        elif opt == "-c":
            s = open(arg, "rb").read()
            x509 = X509()
            x509.parse(s)
            certChain = X509CertChain([x509])
        elif opt == "-u":
            username = arg
        elif opt == "-p":
            password = arg
        elif opt == "-t":
            s = open(arg, "rU").read()
            tack = TACK()
            tack.parsePem(s)
        elif opt == "-b":
            s = open(arg, "rU").read()
            breakSigs = TACK_Break_Sig.parsePemList(s)
        elif opt == "-v":
            verifierDB = VerifierDB(arg)
            verifierDB.open()
        elif opt == "-d":
            directory = arg
        elif opt == "--reqcert":
            reqCert = True
        else:
            assert(False)
            
    if not argv:
        printError("Missing address")
    if len(argv)>1:
        printError("Too many arguments")
    #Split address into hostname/port tuple
    address = argv[0]
    address = address.split(":")
    if len(address) != 2:
        raise SyntaxError("Must specify <host>:<port>")
    address = ( address[0], int(address[1]) )

    # Populate the return list
    retList = [address]
    if "k" in argString:
        retList.append(privateKey)
    if "c" in argString:
        retList.append(certChain)
    if "u" in argString:
        retList.append(username)
    if "p" in argString:
        retList.append(password)
    if "t" in argString:
        retList.append(tack)
    if "b" in argString:
        retList.append(breakSigs)
    if "v" in argString:
        retList.append(verifierDB)
    if "d" in argString:
        retList.append(directory)
    if "reqcert" in flagsList:
        retList.append(reqCert)
    return retList


def clientCmd(argv):
    (address, privateKey, certChain, username, password) = \
        handleArgs(argv, "kcup")
        
    if (certChain and not privateKey) or (not certChain and privateKey):
        raise SyntaxError("Must specify CERT and KEY together")
    if (username and not password) or (not username and password):
        raise SyntaxError("Must specify USER with PASS")
    if certChain and username:
        raise SyntaxError("Can use SRP or client cert for auth, not both")

    #Connect to server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(address)
    connection = TLSConnection(sock)
    
    try:
        start = time.clock()
        if username and password:
            connection.handshakeClientSRP(username, password, reqTack=True)
        else:
            connection.handshakeClientCert(certChain, privateKey,reqTack=True)
        stop = time.clock()        
        print "Handshake success"        
    except TLSLocalAlert, a:
        if a.description == AlertDescription.user_canceled:
            print str(a)
        else:
            raise
        sys.exit(-1)
    except TLSRemoteAlert, a:
        if a.description == AlertDescription.unknown_psk_identity:
            if username:
                print "Unknown username"
            else:
                raise
        elif a.description == AlertDescription.bad_record_mac:
            if username:
                print "Bad username or password"
            else:
                raise
        elif a.description == AlertDescription.handshake_failure:
            print "Unable to negotiate mutually acceptable parameters"
        else:
            raise
        sys.exit(-1)

    print "  Handshake time: %.4f seconds" % (stop - start)
    print "  Version: %s" % connection.getVersionName()
    print("  Cipher: %s %s" % (connection.getCipherName(), 
        connection.getCipherImplementation()))
    if connection.session.srpUsername:
        print("  Client SRP username: %s" % connection.session.srpUsername)
    if connection.session.clientCertChain:
        print("  Client X.509 SHA1 fingerprint: %s" % 
            connection.session.clientCertChain.getFingerprint())
    if connection.session.serverCertChain:
        print("  Server X.509 SHA1 fingerprint: %s" % 
            connection.session.serverCertChain.getFingerprint())
    if connection.session.tack or connection.session.breakSigs:
        print("  TACK:")
        print(writeTextTACKStructures(connection.session.tack, 
                                  connection.session.breakSigs))
    connection.close()


def serverCmd(argv):
    (address, privateKey, certChain, tack, breakSigs, 
        verifierDB, directory, reqCert) = handleArgs(argv, "kctbvd", ["reqcert"])


    if (certChain and not privateKey) or (not certChain and privateKey):
        raise SyntaxError("Must specify CERT and KEY together")
    if tack and not certChain:
        raise SyntaxError("Must specify CERT with TACK")
    
    print("I am an HTTPS test server, I will listen on %s:%d" % 
            (address[0], address[1]))    
    if directory:
        os.chdir(directory)
    print("Serving files from %s" % os.getcwd())
    
    if certChain and privateKey:
        print("Using certificate and private key...")
    if verifierDB:
        print("Using verifier DB...")
    if tack:
        print("Using TACK...")
    if breakSigs:
        print("Using TACK Break Sigs...")
        
    #############
    sessionCache = SessionCache()

    class MyHTTPServer(ThreadingMixIn, TLSSocketServerMixIn, HTTPServer):
        def handshake(self, connection):
            print "About to handshake..."
            try:
                connection.handshakeServer(certChain=certChain,
                                              privateKey=privateKey,
                                              verifierDB=verifierDB,
                                              tack=tack,
                                              breakSigs=breakSigs,
                                              sessionCache=sessionCache)
            except TLSRemoteAlert as a:
                if a.description == AlertDescription.user_canceled:
                    print str(a)
                    return False
                else:
                    raise
            except TLSLocalAlert as a:
                if a.description == AlertDescription.unknown_psk_identity:
                    if username:
                        print "Unknown username"
                        return False
                    else:
                        raise
                elif a.description == AlertDescription.bad_record_mac:
                    if username:
                        print "Bad username or password"
                        return False
                    else:
                        raise
                elif a.description == AlertDescription.handshake_failure:
                    print "Unable to negotiate mutually acceptable parameters"
                    return False
                else:
                    raise
                
            connection.ignoreAbruptClose = True
            print "Handshake success"
            print "  Version: %s" % connection.getVersionName()
            print "  Cipher: %s %s" % (connection.getCipherName(), 
                            connection.getCipherImplementation())
            if connection.session.srpUsername:
                print("  Client SRP username: %s" % 
                        connection.session.srpUsername)
            if connection.session.clientCertChain:
                print("  Client X.509 SHA1 fingerprint: %s" % 
                        connection.session.clientCertChain.getFingerprint())
            if connection.session.serverCertChain:
                print("  Server X.509 SHA1 fingerprint: %s" % 
                        connection.session.serverCertChain.getFingerprint())
            if connection.session.tack or connection.session.breakSigs:
                print("  TACK:")
                print(writeTextTACKStructures(connection.session.tack, 
                                          connection.session.breakSigs,
                                          True))
            return True
    httpd = MyHTTPServer(address, SimpleHTTPRequestHandler)
    httpd.serve_forever()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        printUsage("Missing command")
    elif sys.argv[1] == "client"[:len(sys.argv[1])]:
        clientCmd(sys.argv[2:])
    elif sys.argv[1] == "server"[:len(sys.argv[1])]:
        serverCmd(sys.argv[2:])
    else:
        printUsage("Unknown command: %s" % sys.argv[1])

