# :coding: utf-8
# :copyright: Copyright (c) 2014 ftrack

import json
import tempfile
import base64
import traceback
import os
from uuid import uuid1 as uuid

import ftrack_logging
ftrack_logging.setup()


import logging
logger = logging.getLogger('ftrack_connect_rv')

import rv.commands
import rv.rvtypes
import rv.extra_commands
import rv.rvui
import rv.runtime
import rv as rv

# Cache to keep track of filesystem path for components.
# This will cause the component to use the same filesystem path
# during the entire session.
componentFilesystemPaths = {}
api = None

sequenceSourceNode = None
stackSourceNode = None
layoutSourceNode = None


def getAPI():
    '''Return the ftrack api

    Util method to try to load the ftrack api

    '''
    global api
    if not api:
        try:
            import ftrack as api
            api.setup(actions=False)
            logger.info('ftrack Python API loaded.')
        except ImportError:
            logger.exception(
                'Could not load ftrack Python API. Please check it is '
                'available on the PYTHONPATH.'
            )
            api = None

    return api


def _getSourceNode(nodeType='sequence'):
    '''Return source node of *nodeType*.'''
    global sequenceSourceNode
    global stackSourceNode
    global layoutSourceNode

    if nodeType == 'sequence':
        if sequenceSourceNode is None:
            sequenceSourceNode = rv.commands.newNode(
                'RVSequenceGroup', 'Sequence'
            )

            rv.extra_commands.setUIName(
                sequenceSourceNode, 'SequenceNode'
            )

        return sequenceSourceNode

    elif nodeType == 'stack':
        if stackSourceNode is None:
            stackSourceNode = rv.commands.newNode(
                'RVStackGroup', 'Stack'
            )

            rv.extra_commands.setUIName(
                stackSourceNode, 'StackNode'
            )

        return stackSourceNode

    elif nodeType == 'layout':
        if layoutSourceNode is None:
            layoutSourceNode = rv.commands.newNode(
                'RVLayoutGroup', 'Layout'
            )

            rv.extra_commands.setUIName(
                layoutSourceNode, 'LayoutNode'
            )

        return layoutSourceNode


def _setWipeMode(state):
    '''Util to set the state of wipes instead of toggle.'''
    if rv.runtime.eval('rvui.wipeShown()', ['rvui']) != -1 and state is False:
        rv.runtime.eval('rvui.toggleWipe()', ['rvui'])

    if rv.runtime.eval('rvui.wipeShown()', ['rvui']) == -1 and state is True:
        rv.runtime.eval('rvui.toggleWipe()', ['rvui'])


def _getFilePath(componentId):
    '''Return a single access path based on *source* and *location*

    Generates a filesystem path for the specified *source* and *location* using
    the ftrack location api.

    '''
    global componentFilesystemPaths

    api = getAPI()

    if componentId not in componentFilesystemPaths:
        location = api.pickLocation(componentId)

        if not location:
            raise IOError()

        logger.info(
            'Retrieving fileSystemPath  for component'
            ' "{0}" from location "{1}".'
            .format(
                componentId, str(location.getName())
            )
        )

        component = location.getComponent(componentId)
        componentFilesystemPaths[componentId] = component.getFilesystemPath()

    logger.info(
        'FileSystemPath for component "{0}" is "{1}"'.format(
            componentId, componentFilesystemPaths[componentId]
        )
    )

    return componentFilesystemPaths[componentId]


def _ftrackAddVersion(track, layout):
    stackInputs = rv.commands.nodeConnections(layout, False)[0]
    newSource = rv.commands.addSourceVerbose([track], None)
    rv.commands.setNodeInputs(layout, stackInputs)
    rv.extra_commands.setUIName(
        rv.commands.nodeGroup(newSource), track
    )

    return newSource


def _ftrackCreateGroup(tracks, sourceNode, layout):
    singleSources = []
    for track in tracks:
        singleSources.append(
            rv.commands.nodeGroup(_ftrackAddVersion(track, layout))
        )

    rv.commands.setNodeInputs(
        sourceNode, singleSources
    )


def loadPlaylist(playlist, index=None, includeFrame=None):
    '''Load a playlist into RV.

    Load a specified *playlist* into RV and jump to an optional *index*. If
    *includeFrame* is an optional frame reference.

    '''
    _setWipeMode(False)
    startFrame = None

    if not includeFrame == 'false':
        startFrame = rv.extra_commands.sourceFrame(rv.commands.frame(), None)

    for oldSource in rv.commands.nodesOfType('RVSourceGroup'):
        rv.commands.deleteNode(oldSource)

    sources = []
    for item in playlist:
        sources.append(_getFilePath(
            item.get('componentId')
        ))

    sequenceSourceNode = _getSourceNode('sequence')

    _ftrackCreateGroup(sources, sequenceSourceNode, 'defaultLayout')
    rv.commands.setViewNode(sequenceSourceNode)

    if index:
        ftrackJumpTo(index, startFrame)


def validateComponentLocation(componentId, versionId):
    '''Return if the *componentId* is accessible in a local location.'''
    try:
        _getFilePath(componentId)
    except:
        logger.warning(
            'Component with Id "{0}" is not available in any location.'.format(
                componentId
            )
        )
        try:
            rv.commands.sendInternalEvent(
                'ftrack-event',
                base64.b64encode(
                    json.dumps(
                        {
                            'type': 'breakItem',
                            'versionId': versionId
                        }
                    )
                ),
                None
            )
        except:
            logger.exception(
                'Could not send internal event to ftrack.'
            )


def ftrackCompare(data):
    '''Activate compare mode in RV

    Activiate compare mode of *type* between *componentIdA* and *componentIdB*

    '''
    _setWipeMode(False)
    startFrame = None
    try:
        startFrame = rv.extra_commands.sourceFrame(rv.commands.frame(), None)
    except:
        pass

    componentIdA = data.get('componentIdA')
    componentIdB = data.get('componentIdB')
    mode = data.get('mode')

    trackA = _getFilePath(componentIdA)

    layout = 'defaultStack' if mode == 'wipe' else 'defaultLayout'

    if not mode == 'load':
        trackB = _getFilePath(componentIdB)

        try:
            if mode == 'wipe':
                sourceNode = _getSourceNode('stack')
                _ftrackCreateGroup([trackA, trackB], sourceNode, layout)
                rv.commands.setViewNode(sourceNode)
                rv.runtime.eval('rvui.toggleWipe()', ['rvui'])
            else:
                sourceNode = _getSourceNode('layout')
                _ftrackCreateGroup([trackA, trackB], sourceNode, layout)
                rv.commands.setViewNode(sourceNode)
        except:
            print traceback.format_exc()
    else:
        sourceNode = _getSourceNode('layout')
        _ftrackCreateGroup([trackA], sourceNode, layout)
        rv.commands.setViewNode(sourceNode)

    if startFrame > 1:
        rv.commands.setFrame(startFrame)


def _getEntityFromEnvironment():
    # Check for environment variable specifying additional information to
    # use when loading.
    eventEnvironmentVariable = 'FTRACK_CONNECT_EVENT'

    eventData = os.environ.get(eventEnvironmentVariable)
    if eventData is not None:
        try:
            decodedEventData = json.loads(base64.b64decode(eventData))
        except (TypeError, ValueError):
            logger.exception(
                'Failed to decode {0}: {1}'
                .format(eventEnvironmentVariable, eventData)
            )
        else:
            selection = decodedEventData.get('selection', [])

            # At present only a single entity which should represent an
            # ftrack List is supported.
            if selection:
                try:
                    entity = selection[0]
                    entityId = entity.get('entityId')
                    entityType = entity.get('entityType')
                    return entityId, entityType
                except (IndexError, AttributeError, KeyError):
                    logger.exception(
                        'Failed to extract selection information from: {0}'
                        .format(selection)
                    )
    else:
        logger.debug(
            'No event data retrieved. {0} not set.'
            .format(eventEnvironmentVariable)
        )

    return None, None


def getNavigationURL(params=None):
    '''Return URL to navigation panel based on *params*.'''
    return _generateURL(params, 'review_navigation')


def getActionURL(params=None):
    '''Return URL to action panel based on *params*.'''
    return _generateURL(params, 'review_action')


def _generateURL(params=None, panelName=None):
    '''Return URL to panel in ftrack based on *params* or *panel*.'''
    entityId = None
    entityType = None

    url = ''
    if params:
        panelName = panelName or params

        try:
            params = json.loads(params)
            entityId = params['entityId'][0]
            entityType = params['entityType'][0]
        except Exception:
            entityId, entityType = _getEntityFromEnvironment()

        try:
            url = getAPI().getWebWidgetUrl(
                panelName, 'tf', entityId=entityId, entityType=entityType
            )
        except Exception as exception:
            logger.exception(str(exception))

    logger.info('Returning url "{0}"'.format(url))

    return url


def ftrackFilePath(id):
    try:
        if id != "":
            filename = "%s.jpg" % id
            filepath = os.path.join(tempfile.gettempdir(), filename)
        else:
            filepath = tempfile.gettempdir()
        return filepath
    except:
        print traceback.format_exc()
        return ""


def ftrackUUID(short):
    '''Retun a uuid based on uuid1
    '''
    return str(uuid())


def ftrackGetAttachmentId(s):
    obj = json.loads(s)
    return str(obj.get('attachment',{}).get('attachmentid',''))


def ftrackJumpTo(index=0, startFrame=1):
    '''Move playhead to an index

    Moves the RV playhead to the specified *index*

    '''
    index = int(index)
    frameNumber = 0

    for idx,source in enumerate(rv.commands.nodesOfType('RVFileSource')):
        if not idx >= index:
            data = rv.commands.sourceMediaInfoList(source)[0]
            add = (data.get('endFrame',0) - data.get('startFrame',0)) + 1
            add = 1 if add == 0 else add
            frameNumber += (add)

    rv.commands.setFrame(frameNumber + startFrame)