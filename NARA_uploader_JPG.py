#!/usr/bin/python
# -*- coding: utf-8  -*-
'''
Bot to upload NARA images to Commons.
 
The bot expects a directory containing the images on the commandline and a text file containing the mappings.
 
The bot uses http://toolserver.org/~slakr/archives.php to get the description
'''
 
convertCmdPath = '/opt/local/bin/convert'
 
import sys, os.path, hashlib, base64, glob, re, urllib, time, unicodedata
sys.path.append("/Users/Dominic/pywikipedia")
import wikipedia, config, query, upload
import shutil, socket
import subprocess
 
########################################################
### start effbot code
### source: http://effbot.org/zone/re-sub.htm#unescape-html
########################################################
#import re, htmlentitydefs
import htmlentitydefs
 
##
# Removes HTML or XML character references and entities from a text string.
#
# @param text The HTML (or XML) source text.
# @return The plain text, as a Unicode string, if necessary.
 
def unescape(text):
    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # character reference
            try:
                if text[:3] == "&#x":
                    return unichr(int(text[3:-1], 16))
                else:
                    return unichr(int(text[2:-1]))
            except ValueError:
                pass
        else:
            # named entity
            try:
                text = unichr(htmlentitydefs.name2codepoint[text[1:-1]])
            except KeyError:
                pass
        return text # leave as is
    return re.sub("&#?\w+;", fixup, text)
########################################################
### end effbot code
########################################################
 
 
########################################################
### Start template parsing code
########################################################
 
def find_template( name, wikitext):
 
    def find_fields(templateText):
        """
        Returns the contents of a given template, in a list of (parameter, value) elements
 
            templateText : the raw text of the template
        """
 
        def addParameter():
            fields[param_name.strip()]={'val':param_value.strip(), 'index':param_count }
            return param_count + 1
 
 
        fields = {}
 
        brace_count = -2 #count of {, ignore the ones around {{template}}
        sq_brk_count = 0 #count of [
        ang_brk_count = 0 #count of <
 
        #state machine states
        OUTSIDE = 0
        IN_NAME = 1
        IN_VAL = 2
 
        state = OUTSIDE
 
        param_name = ''
        param_value = ''
        param_count = 0
 
        for char in templateText:
 
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
 
                if brace_count == -1: #end of template
                    try:
                        param_count = addParameter()
                    except UnboundLocalError:
                        pass #maybe we have no parameters
 
            elif char == '[':
                sq_brk_count += 1
            elif char == ']':
                sq_brk_count -= 1
 
            elif char == '<':
                ang_brk_count += 1
            elif char == '>':
                ang_brk_count -= 1
 
            elif char == '|' and brace_count == 0 and sq_brk_count == 0:
            # we have a pipe in the header template
                if param_name: #we have a parameter from before:
                    param_count = addParameter()
 
                param_name = '' #reset name
                state = IN_NAME #set state
                continue #skip "|" char
 
            elif char == '=' and sq_brk_count == 0 and state == IN_NAME:
            # we have an equals after a parameter name
 
                state = IN_VAL
                param_value = '' #reset the parameter field value
                continue
 
 
            if state == IN_NAME:
                param_name += char
 
            elif state == IN_VAL:
                param_value += char
 
        return fields
 
    def find_matching_braces( text, first_brace_index ):
        """
        Finds the index of the matching right brace to a left brace
 
            text: text to search in
            first_brace_index : index of the left brace to look for a partner
 
        Returns:
            If a matching brace is found, returns the index of it
            If no matching brace is found, returns None
        """
 
        lbrace = text[first_brace_index]
 
        if lbrace == '{':
            rbrace = '}'
        elif lbrace == '[':
            rbrace = ']'
        elif lbrace == '(':
            rbrace = ')'
        elif lbrace == '<':
            rbrace == '>'
        else:
            print('"%s" is not a brace, cannot find a partner.'% text[first_brace_index])
            return None
 
        # brace counter
        count = 0
        char_number = first_brace_index
 
        for char in text[first_brace_index:]:
            if char == lbrace:
                count += 1
            elif char == rbrace:
                count -= 1
            elif count == 0:
                break
 
            char_number += 1
 
        if count != 0:
            print('Cannot find a partner for "%s" in the string.'% text[first_brace_index])
            return None
        else:
            return char_number
 
    templates = []
 
    regex = r'({{\s*[' + name[0].lower() + name[0].upper() + ']' + name[1:] +')'
 
    templateRanges = re.finditer(regex, wikitext)
 
    for templateRange in templateRanges:
 
        template_start = templateRange.start()
        template_end = find_matching_braces(wikitext, template_start)
 
        if not template_end: #malformed - missing braces
            continue
 
        template = wikitext[template_start:template_end]        
 
        fields = find_fields(template)
 
        templateData = {'name':name, 'fields':fields, 'start':template_start, 'end':template_end}
 
        templates.append( templateData )
 
    return templates
 
def format_fields( name, fields, compact=False):
 
    fieldList = [ (fields[entry]['index'], entry, fields[entry]['val']) for entry in fields ]
 
    fieldList.sort()
 
    longestField = max( [ len(x[1]) for x in fieldList] )
 
    templateText = '{{' + name
 
    for index, paramName, paramVal in fieldList:
 
        if not compact: templateText += '\n'
        templateText += ' | '
 
        spacing = '' if compact else ' '*(longestField-len(paramName) )
        templateText += paramName + spacing + ' = ' + paramVal
 
    if not compact: templateText += '\n'
    templateText += '}}'
 
    return templateText
 
 
########################################################
### End template parsing code
########################################################
 
def getRecords(textfile):
    result = {}
    f = open(textfile, "r")
 
    for line in f.readlines():
        (filename, sep, arc) = line.partition(u' ')
        result[filename] = int(arc.strip())
 
    return result
 
 
def findDuplicateImagesByHash(filename, site = wikipedia.getSite(u'commons', u'commons')):
    '''
    Takes the photo, calculates the SHA1 hash and asks the mediawiki api for a list of duplicates.
 
    TODO: Add exception handling, fix site thing
    '''
    f = open(filename, 'rb')
 
    hashObject = hashlib.sha1()
    hashObject.update(f.read(-1))
    return site.getFilesFromAnHash(base64.b16encode(hashObject.digest()))
 
def findDuplicateImagesByName(filename, site = wikipedia.getSite(u'commons', u'commons')):
 
    try:
        text = wikipedia.Page(site, 'File:' + filename).get()
    except wikipedia.NoPage:
        return [] #no file by that name
 
    return [filename] # duplicate exists, return it
 
def addDuplicatesToList(fileInfo, foundDuplicates, duplicateFiletypes):
 
    if len(foundDuplicates) > 0:
        duplicateFile = foundDuplicates.pop()
        duplicateFiletypes[fileInfo['ext']]=duplicateFile
        wikipedia.output(u'Found duplicate of %s at %s' % (fileInfo['name'], duplicateFile) )
 
    return duplicateFiletypes
 
 
def fetchDescriptionFromWebtool(fileId):
 
    # No metadata handling. We use a webtool
    description = ''
    descriptionFetchTries = 0
 
    wikipedia.output(u'Attempting to fetch description for file ID %d from the webtool.' % fileId)
 
    while True:
        description = getDescription(fileId)
        descriptionFetchTries += 1 
 
        if not description:
            if descriptionFetchTries > 10:
                wikipedia.output(u'Decription text cannot be found for this file.')
                break
            else:
                wikipedia.output(u'No reply from the webtool, retrying.')
        else:
            wikipedia.output(u'Decription text found.')
            break
 
    return description
 
def getDescription(fileId):
    """
    fileId  : the ARC ID
    """
 
    url = u'http://toolserver.org/~slakr/archives.php?archiveHint=%s' % (fileId,)
 
 
    textareaRe = re.compile('^<textarea rows="\d+" cols="\d+">(.+)</textarea>$', re.MULTILINE + re.DOTALL)
 
    gotInfo = False
    matches = None
    maxtries = 10
    tries = 0
    while(not gotInfo):
        try:
            if ( tries < maxtries ):
                tries = tries + 1
                archivesPage = urllib.urlopen(url)
                matches = textareaRe.search(archivesPage.read().decode('utf-8'))
                gotInfo = True
            else:
                break
        except IOError:
            wikipedia.output(u'Got an IOError, let\'s try again')
        except socket.timeout:
            wikipedia.output(u'Got a timeout, let\'s try again')
 
    if (matches and gotInfo):
        description =  unescape(matches.group(1))
        return description
    return u''
 
def getTitle(fileId, description):
    titleRe = re.compile('^\|Title=(.+)$', re.MULTILINE)
    titleMatch = titleRe.search(description)
 
    if titleMatch:
        titleText = truncateWithEllipsis(titleMatch.group(1), 120, "...")
        title = u'%s - NARA - %s.tif' % (titleText, fileId)
        return cleanUpTitle(title)
    else:
        wikipedia.output(u'No title found in the webtool output!' )
        return False
 
def cleanUpTitle(title):
    """
    Clean up the title of a potential mediawiki page. Otherwise the title of
    the page might not be allowed by the software.
    """
 
    title = title.strip()
    title = re.sub(u"[<{\\[]", u"(", title)
    title = re.sub(u"[>}\\]]", u")", title)
    title = re.sub(u"[ _]?\\(!\\)", u"", title)
    title = re.sub(u",:[ _]", u", ", title)
    title = re.sub(u"[;:][ _]", u", ", title)
    title = re.sub(u"[\t\n ]+", u" ", title)
    title = re.sub(u"[\r\n ]+", u" ", title)
    title = re.sub(u"[\n]+", u"", title)
    title = re.sub(u"[?!]([.\"]|$)", u"\\1", title)
    title = re.sub(u"[#%?!]", u"^", title)
    title = re.sub(u"[;]", u",", title)
    title = re.sub(u"[/+\\\\:]", u"-", title)
    title = re.sub(u"--+", u"-", title)
    title = re.sub(u",,+", u",", title)
    title = re.sub(u"[-,^]([.]|$)", u"\\1", title)
    title = title.replace(u" ", u"_")
    return title
 
def truncateWithEllipsis(s, limit, ellipsis=u"\u2026"):
    if len(s) > limit:
        for i in range(limit, 0, -1):
            if (unicodedata.category(s[i]) == 'Zs'
                and i + len(ellipsis) <= limit):
                return s[:i] + ellipsis
        return s[:-len(ellipsis)] + ellipsis
    else:
        return s
 
def createDerivatives(sourcefilename, derivativeDirectory):
    """
    Create any derivative files needed.
 
    TIFFs will be converted to JPGs
    """
 
 
    def makeDerivative(convertExt):
 
        def makeDerivativeName():
            return os.path.join(derivativeDirectory, srcName + convertExt)
 
        filelist.append( {'ext':srcExt.lower(), 'name':sourcefilename})
        derivativeName = makeDerivativeName()
 
        #generate if they don't exist already
        if not os.path.exists(derivativeName):
            cmd = [convertCmdPath, sourcefilename, '-quality', '100', derivativeName]
            subprocess.call(cmd)
 
        filelist.append({'ext':convertExt, 'name':derivativeName})
 
 
    srcHead, srcTail = os.path.split(sourcefilename)
    srcName, srcExt = os.path.splitext(srcTail)
 
    filelist = []
    # if the filetype needs a jpg creating
    if srcExt.lower() in ['.tif', '.tiff']:
        makeDerivative('.jpg')
 
 
    return filelist
 
def setDestinations(fileList, title):
    """
    Set the destinations for the derivative files based on the title
    """
    newList = []
    titleRoot, titleExt = os.path.splitext(title)
 
    for fileInfo in fileList:
        fileInfo['dest'] = titleRoot + fileInfo['ext']
 
        newList.append(fileInfo)
 
    return newList
 
def createDerivativeGallery(fileList, title):
    """
    Constructs a gallery of derivative files
 
        fileList : list of pairs of src/dest files: (local filepath, destination filename)
        title    : intendend destination filename
    """
    gallery = ''
    if len(fileList)>1:
 
        gallery += '<gallery>'
 
        for fileInfo in fileList:
 
            gallery += '\nFile:%s|%s' % (fileInfo['dest'], fileInfo['ext'])
        gallery += '\n</gallery>'
 
    return gallery
 
def addDerivativesToDescription(description, gallery, title):
    """
    If there are any derivative files, add a gallery of them to the
    description under "Other versions"
 
        gallery    : gallery to add to the description
        description: raw description text
    """
 
    try:
        infoTemplate = find_template( 'NARA-image-full', description)[0]
    except:
        return False #we didn't find a template to update
 
    otherVersionsParam = None
    for name in ['Other_versions', 'other_versions', 'Other versions', 'other versions']:
        if name in infoTemplate['fields']:
            otherVersionsParam = name
 
    if otherVersionsParam:
        otherVersions = infoTemplate['fields'][otherVersionsParam]['val']
    else:        
        wikipedia.output(u"Couldn't find the 'other versions' parameter.")
        otherVersionsParam = 'Other_versions'
        otherVersions = '' #create the parameter
        infoTemplate['fields'][otherVersionsParam] = {'val':otherVersions, 'index':100}
 
 
    m = re.search(r'<gallery>', otherVersions)
 
    if m:
        return False #there is a gallery already
    else:
        otherVersions = gallery + otherVersions 
 
    infoTemplate['fields'][otherVersionsParam]['val'] = otherVersions
 
    #reinsert modified template
    description = (description[0:infoTemplate['start']] +
        format_fields( 'NARA-image-full', infoTemplate['fields'] ) +
        description[infoTemplate['end']:] )
 
    return description
 
 
def removeTIFFParameter(description, type):
    """
    Returns a description without the TIFF parameter if the file is 
    not a TIFF
    """
 
    isTiff = type.lower() in ['.tiff','.tif']
 
    if not isTiff:
        return re.sub(r'(\s*\|\s*TIFF\s*=)\s*yes', r'\1', description)
 
    else:
        return description
 
def main(args):
    '''
    Main loop.
    '''
    workdir = u''
    textfile = u''
    records = {}
 
 
    site = wikipedia.getSite(u'commons', u'commons')
    wikipedia.setSite(site)
 
    if  (len(args) < 3):
        wikipedia.output(u'Too few arguments. Usage: NARA_uploader.py <original dir> <textfile> <derivative dir> [start filename]')
        sys.exit()
 
    if os.path.isdir(args[0]):
        workdir = args[0]
    else:
        wikipedia.output(u'%s doesn\'t appear to be a directory. Exiting.' % (args[0],))
        sys.exit()
 
    derivativeDirectory = args[2]
    if os.path.exists(derivativeDirectory) and not os.path.isdir(derivativeDirectory):
        wikipedia.output(u"%s exists, but isn't a directory. Exiting." % derivativeDirectory)
        sys.exit()
    elif not os.path.exists(derivativeDirectory):
        wikipedia.output(u'%s doesn\'t appear to exist. Creating.' % derivativeDirectory)
        os.mkdir(derivativeDirectory)
 
    try:
        startFile = args[3]
        startFileFound = False
 
        startPath = os.path.join(workdir, startFile)
 
        if not os.path.exists(startPath) or os.path.isdir(startPath):
            wikipedia.output(u"%s doesn't exist, or it is directory. Exiting." % startPath)
            sys.exit()
 
    except IndexError:
        startFile = None
 
 
    textfile = args[1]
    records = getRecords(textfile)
    #print records
 
    sourcefilenames = glob.glob(workdir + u"/*.TIF")
    sourcefilenames.sort()
 
    for sourcefilename in sourcefilenames:
 
        wikipedia.output(u'\nProcessing %s' % sourcefilename)
 
        if startFile: #if we want to skip to a file
            fileHead, fileTail = os.path.split(sourcefilename)
 
            if not startFileFound:
                if fileTail != startFile:
                    wikipedia.output('Skipping %s' % sourcefilename)
                    continue
                else: #we have fond the start point
                    startFileFound = True
 
        filename = os.path.basename(sourcefilename)
        # This will give an ugly error if the id is unknown
        if not records.get(filename):
             wikipedia.output(u'Can\'t find %s in %s. Skipping this file.' % (filename, textfile))
        elif os.path.getsize(sourcefilename) >= 1024 * 1024 * 100:
             wikipedia.output(u'%s too big. Skipping this file.' % (sourcefilename,))
        else:
            fileId = records.get(filename)
 
            wikipedia.output(u'Found file ID: %d' % fileId)
 
 
            #generate all the files we might need to upload
            filesToUpload = createDerivatives(sourcefilename, derivativeDirectory)
 
 
            duplicateFiletypes = {}
            #check for duplicates of the original on wiki
            for fileInfo in filesToUpload:   
 
                if fileInfo['ext'] != '.tif' :
                    continue
 
                foundDuplicates = findDuplicateImagesByHash(fileInfo['name'])
 
                duplicateFiletypes = addDuplicatesToList(fileInfo, foundDuplicates, duplicateFiletypes)
 
            # follow the naming + description from the tif if it exists, or make it up from the description
            if '.tif' in duplicateFiletypes:
                title = duplicateFiletypes['.tif']
 
                wikipedia.output(u'Fetching description from TIF file page: %s' % title )
                description = wikipedia.Page(site, 'File:' + title).get()
 
            else:
                description = fetchDescriptionFromWebtool(fileId)
 
                if not description:
                    wikipedia.output(u'No description! Skipping this file.' )
                    continue
                else:
                    categories = u'{{Uncategorized-NARA|year={{subst:CURRENTYEAR}}|month={{subst:CURRENTMONTHNAME}}|day={{subst:CURRENTDAY}}}}\n'
                    description = description + categories
 
                    title = getTitle(fileId, description)
 
                    if not title:
                        continue
 
            #check for duplicates of the derivatives (using the filename we just made)
            for fileInfo in filesToUpload:   
 
                if fileInfo['ext'] == '.tif' :
                    continue
 
                titleRoot, ext = os.path.splitext(title)
                fileTitle = titleRoot + fileInfo['ext']
 
                foundDuplicates = findDuplicateImagesByName(fileTitle)
 
                duplicateFiletypes = addDuplicatesToList(fileInfo, foundDuplicates, duplicateFiletypes)
 
            #construct the gallery
            filesToUpload = setDestinations(filesToUpload, title)
            gallery = createDerivativeGallery(filesToUpload, title)
 
            #for every file, including original and derivatives
            for fileInfo in filesToUpload:
 
                titleRoot, ext = os.path.splitext(title)
                fileTitle = titleRoot + fileInfo['ext']
 
                if fileInfo['ext'] in duplicateFiletypes: #we have a duplicate: add derivs if needed
 
                    currentFilename = duplicateFiletypes[fileInfo['ext']]
 
                    currentFilePage = wikipedia.Page(site, 'File:' + currentFilename)
 
                    currentDescription = currentFilePage.get()
 
                    currentDescription = addDerivativesToDescription(currentDescription, gallery, title)
 
                    if currentDescription:
                        wikipedia.output('Updating the description for %s:\n\n%s' % (currentFilename, currentDescription))
                        currentFilePage.put( currentDescription, comment="Adding other versions to the description." )
                    else:
                        wikipedia.output('Gallery exists on page %s' % currentFilename)
 
                else: #upload the file with generated info   
 
                    wikipedia.output(fileInfo['name'] +' --> '+ fileInfo['dest'])
 
                    newDescription = addDerivativesToDescription(description, gallery, title)
 
                    if newDescription: #if the gallery add failed due to existing gallery, just carry on with the original
                        description = newDescription
 
                    fileDescription = removeTIFFParameter(description, fileInfo['ext'])
                    wikipedia.output(fileDescription)
                    bot = upload.UploadRobot(url=fileInfo['name'].decode(sys.getfilesystemencoding()), description=fileDescription, useFilename=fileInfo['dest'], keepFilename=True, verifyDescription=False)
                    bot.run()
 
if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    finally:
        print u'All done'
