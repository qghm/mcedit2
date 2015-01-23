"""
    select
"""
from __future__ import absolute_import, division, print_function, unicode_literals
import logging

from OpenGL import GL
from PySide import QtGui, QtCore
import numpy

from mcedit2.command import SimpleRevisionCommand
from mcedit2.editorcommands.fill import fillCommand
from mcedit2.editorcommands.replace import replaceCommand
from mcedit2.editortools import EditorTool
from mcedit2.rendering import cubes
from mcedit2.rendering.selection import SelectionScene, SelectionFaceNode, SelectionBoxNode
from mcedit2.util.load_ui import load_ui
from mcedit2.util.glutils import gl
from mcedit2.rendering.depths import DepthOffset
from mcedit2.rendering import scenegraph, rendergraph
from mcedit2.util.showprogress import showProgress
from mcedit2.widgets import shapewidget
from mcedit2.widgets.layout import Column
from mcedit2.widgets.shapewidget import ShapeWidget
from mcedit2.worldview.worldview import boxFaceUnderCursor
from mceditlib import faces
from mceditlib.geometry import Vector
from mceditlib.selection import BoundingBox
from mceditlib.operations import ComposeOperations
from mceditlib.operations.entity import RemoveEntitiesOperation
from mceditlib import selection

log = logging.getLogger(__name__)


class SelectionCoordinateWidget(QtGui.QWidget):
    def __init__(self, *args, **kwargs):
        super(SelectionCoordinateWidget, self).__init__(*args, **kwargs)
        load_ui("selection_coord_widget.ui", baseinstance=self)

        self.xMinInput.valueChanged.connect(self.setMinX)
        self.yMinInput.valueChanged.connect(self.setMinY)
        self.zMinInput.valueChanged.connect(self.setMinZ)
        self.xMaxInput.valueChanged.connect(self.setMaxX)
        self.yMaxInput.valueChanged.connect(self.setMaxY)
        self.zMaxInput.valueChanged.connect(self.setMaxZ)

        self.editSizeInput.stateChanged.connect(self.editSizeClicked)

    def editSizeClicked(self, state):
        if state:
            size = self.boundingBox.size
            self.widthLabel.setText("W:")
            self.heightLabel.setText("H:")
            self.lengthLabel.setText("L:")

        else:
            size = self.boundingBox.maximum
            self.widthLabel.setText("to")
            self.heightLabel.setText("to")
            self.lengthLabel.setText("to")

        self.xMaxInput.setValue(size[0])
        self.yMaxInput.setValue(size[1])
        self.zMaxInput.setValue(size[2])

        minVal = 0 if state else -2000000000
        self.xMaxInput.setMinimum(minVal)
        self.yMaxInput.setMinimum(minVal)
        self.zMaxInput.setMinimum(minVal)


    boxChanged = QtCore.Signal(BoundingBox)

    _boundingBox = BoundingBox()
    @property
    def boundingBox(self):
        return self._boundingBox

    @boundingBox.setter
    def boundingBox(self, box):
        self.setEnabled(box is not None)
        self._boundingBox = box
        if box is not None:
            self.xMinInput.setValue(box.minx)
            self.yMinInput.setValue(box.miny)
            self.zMinInput.setValue(box.minz)

            if self.editSizeInput.checkState():
                self.xMaxInput.setValue(box.width)
                self.yMaxInput.setValue(box.height)
                self.zMaxInput.setValue(box.length)
            else:
                self.xMaxInput.setValue(box.maxx)
                self.yMaxInput.setValue(box.maxy)
                self.zMaxInput.setValue(box.maxz)


    def setMinX(self, value):
        origin, size = self.boundingBox
        origin = value, origin[1], origin[2]
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)


    def setMinY(self, value):
        origin, size = self.boundingBox
        origin = origin[0], value, origin[2]
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)

    def setMinZ(self, value):
        origin, size = self.boundingBox
        origin = origin[0], origin[1], value
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)

    def setMaxX(self, value):
        origin, size = self.boundingBox
        if self.editSizeInput.checkState():
            size = value, size[1], size[2]
        else:
            size = value - origin[0], size[1], size[2]
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)

    def setMaxY(self, value):
        origin, size = self.boundingBox
        if self.editSizeInput.checkState():
            size = size[0], value, size[2]
        else:
            size = size[0], value - origin[1], size[2]
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)

    def setMaxZ(self, value):
        origin, size = self.boundingBox
        if self.editSizeInput.checkState():
            size = size[0], size[1], value
        else:
            size = size[0], size[1], value - origin[2]
        box = BoundingBox(origin, size)
        self.boundingBox = box
        self.boxChanged.emit(box)



class SelectCommand(QtGui.QUndoCommand):
    def __init__(self, selectionTool, box, *args, **kwargs):
        QtGui.QUndoCommand.__init__(self, *args, **kwargs)
        self.setText("Box Select")
        self.box = box
        self.selectionTool = selectionTool
        self.previousBox = None

    def undo(self):
        self.selectionTool.currentSelection = self.previousBox

    def redo(self):
        self.previousBox = self.selectionTool.currentSelection
        self.selectionTool.currentSelection = self.box

class SelectionTool(EditorTool):
    name = "Select"
    iconName = "select_blocks"

    def __init__(self, editorSession, *args, **kwargs):
        """
        :type editorSession: EditorSession
        """
        super(SelectionTool, self).__init__(editorSession, *args, **kwargs)
        toolWidget = QtGui.QWidget()

        editorSession.selectionChanged.connect(self.selectionDidChange)

        self.toolWidget = toolWidget

        self.coordInput = SelectionCoordinateWidget()
        self.coordInput.boxChanged.connect(self.coordInputChanged)
        self.shapeInput = ShapeWidget()
        self.shapeInput.shapeChanged.connect(self.shapeDidChange)
        self.deselectButton = QtGui.QPushButton(self.tr("Deselect"))
        self.deselectButton.clicked.connect(self.deselect)
        self.deleteSelectionButton = QtGui.QPushButton(self.tr("Delete"))
        self.deleteSelectionButton.clicked.connect(self.deleteSelection)
        self.deleteBlocksButton = QtGui.QPushButton(self.tr("Delete Blocks"))
        self.deleteBlocksButton.clicked.connect(self.deleteBlocks)
        self.deleteEntitiesButton = QtGui.QPushButton(self.tr("Delete Entities"))
        self.deleteEntitiesButton.clicked.connect(self.deleteEntities)
        self.fillButton = QtGui.QPushButton(self.tr("Fill"))
        self.fillButton.clicked.connect(self.fill)
        self.replaceButton = QtGui.QPushButton(self.tr("Replace"))
        self.replaceButton.clicked.connect(self.replace)

        self.toolWidget.setLayout(Column(self.coordInput,
                                         self.shapeInput,
                                         self.deselectButton,
                                         self.deleteSelectionButton,
                                         self.deleteBlocksButton,
                                         self.deleteEntitiesButton,
                                         self.fillButton,
                                         self.replaceButton,
                                         None))

        self.cursorNode = SelectionCursor()
        self.overlayNode = scenegraph.Node()
        self.faceHoverNode = SelectionFaceNode()
        self.selectionNode = SelectionScene()
        self.selectionNode.dimension = editorSession.currentDimension  # xxx dimensionDidChange
        self.overlayNode.addChild(self.selectionNode)
        self.overlayNode.addChild(self.faceHoverNode)

        self.boxHandleNode = BoxHandle()
        self.boxHandleNode.boundsChanged.connect(self.boxHandleResized)
        self.boxHandleNode.boundsChangedDone.connect(self.boxHandleResizedDone)
        self.overlayNode.addChild(self.boxHandleNode)

        self.newSelectionNode = None

    def shapeDidChange(self):
        if self.currentSelection is not None:
            self.currentSelection = self.createShapedSelection(self.currentSelection)

    def toolActive(self):
        self.boxHandleNode.boxNode.wireColor = 1, 1, 1, .5

    def toolInactive(self):
        self.faceHoverNode.visible = False
        self.boxHandleNode.boxNode.wireColor = 1, 1, 1, .33

    @property
    def hideSelectionWalls(self):
        return not self.selectionNode.filled

    @hideSelectionWalls.setter
    def hideSelectionWalls(self, value):
        self.selectionNode.filled = not value

    @property
    def currentSelection(self):
        return self.editorSession.currentSelection

    @currentSelection.setter
    def currentSelection(self, value):
        self.editorSession.currentSelection = value

        self.boxHandleNode.bounds = None if value is None else BoundingBox(value.origin, value.size)

    def coordInputChanged(self, box):
        self.currentSelection = self.createShapedSelection(box)

    def selectionDidChange(self, value):
        self.coordInput.boundingBox = value
        self.updateNodes()

    def updateNodes(self):
        box = self.currentSelection
        if box:
            self.selectionNode.visible = True
            self.selectionNode.selection = box
        else:
            self.selectionNode.visible = False
            self.faceHoverNode.visible = False

    def deleteSelection(self):
        command = SimpleRevisionCommand(self.editorSession, "Delete")
        with command.begin():
            fillTask = self.editorSession.currentDimension.fillBlocksIter(self.editorSession.currentSelection, "air")
            entitiesTask = RemoveEntitiesOperation(self.editorSession.currentDimension, self.editorSession.currentSelection)
            task = ComposeOperations(fillTask, entitiesTask)
            showProgress("Deleting...", task)
        self.editorSession.pushCommand(command)

    def deleteBlocks(self):
        pass

    def deleteEntities(self):
        pass

    def fill(self):
        fillCommand(self.editorSession)

    def replace(self):
        replaceCommand(self.editorSession)

    def boxHandleResized(self, box):
        if box is not None:
            self.selectionNode.selection = self.createShapedSelection(box)

    def boxHandleResizedDone(self, box, newSelection):
        if box is not None:
            selection = self.createShapedSelection(box)
            command = SelectCommand(self, selection)
            if not newSelection:
                command.setText(self.tr("Resize Selection"))
            self.editorSession.undoStack.push(command)
            self.updateNodes()

    def mousePress(self, event):
        self.boxHandleNode.mousePress(event)

    def mouseMove(self, event):
        self.mouseDrag(event)

    def mouseDrag(self, event):
        # Update cursor
        self.cursorNode.point = event.blockPosition
        self.cursorNode.face = event.blockFace

        self.boxHandleNode.mouseMove(event)

    def mouseRelease(self, event):
        self.boxHandleNode.mouseRelease(event)


    def deselect(self):
        editor = self.editorSession
        command = SelectCommand(self, None)
        command.setText(self.tr("Deselect"))
        editor.undoStack.push(command)

    selectionColor = (0.8, 0.8, 1.0)
    alpha = 0.33

    showPreviousSelection = True

    def createShapedSelection(self, box):
        if self.shapeInput.currentShape is shapewidget.Square:
            return box
        else:
            return selection.ShapedSelection(box, self.shapeInput.currentShape.shapeFunc)

class BoxHandle(scenegraph.Node, QtCore.QObject):
    dragResizeFace = None
    dragResizeDimension = None
    dragResizePosition = None

    dragStartPoint = None
    dragStartFace = None

    oldBounds = None

    hiliteFace = True

    def __init__(self):
        """
        A drawable, resizable box that can respond to mouse events. Emits boundsChanged whenever its bounding box
        changes, and emits boundsChangedDone when the mouse button is released.
        :return:
        :rtype:
        """
        super(BoxHandle, self).__init__()
        self.boxNode = SelectionBoxNode()
        self.boxNode.filled = False
        self.faceDragNode = SelectionFaceNode()
        self.addChild(self.boxNode)
        self.addChild(self.faceDragNode)

    boundsChanged = QtCore.Signal(BoundingBox)
    boundsChangedDone = QtCore.Signal(BoundingBox, bool)

    @property
    def bounds(self):
        return self.boxNode.selectionBox

    @bounds.setter
    def bounds(self, box):
        if box != self.boxNode.selectionBox:
            self.boxNode.selectionBox = box
            self.faceDragNode.selectionBox = box
            self.boundsChanged.emit(box)

    def dragResizePoint(self, ray):
        # returns a point representing the intersection between the mouse ray
        # and an imaginary plane perpendicular to the dragged face

        """

        :type ray: Ray
        :rtype: Vector
        """
        nearPoint, normal = ray

        dim = self.dragResizeDimension
        distance = self.dragResizePosition - nearPoint[dim]

        scale = distance / (normal[dim] or 0.0001)
        point = normal * scale + nearPoint
        return point

    def boxFromDragResize(self, box, ray):
        point = self.dragResizePoint(ray)

        side = self.dragResizeFace & 1
        dragdim = self.dragResizeFace >> 1


        o, s = list(box.origin), list(box.size)
        if side:
            o[dragdim] += s[dragdim]
        s[dragdim] = 0

        otherSide = BoundingBox(o, s)
        o[dragdim] = int(numpy.floor(point[dragdim] + 0.5))
        thisSide = BoundingBox(o, s)

        return thisSide.union(otherSide)

    def boxFromDragSelect(self, ray):
        """
        Create a flat selection from dragging the mouse outside the selection.

        :type ray: mcedit2.util.geometry.Ray
        :rtype: BoundingBox
        """
        point = self.dragStartPoint
        face = self.dragStartFace
        size = [1, 1, 1]

        dim = face >> 1
        size[dim] = 0
        s = [0,0,0]

        if face & 1 == 0:
            s[dim] = 1
            point = point + s

        startBox = BoundingBox(point, size)
        endPoint = ray.intersectPlane(dim, point[dim])
        endBox = BoundingBox(endPoint.intfloor(), size)

        return startBox.union(endBox)

    def mousePress(self, event):

        # Find side of existing selection to drag
        # xxxx can't do this with disjoint selections?
        if self.bounds is not None:
            point, face = boxFaceUnderCursor(self.bounds, event.ray)

            if face is not None:
                log.info("Beginning drag resize")
                self.dragResizeFace = face
                # Choose a dimension perpendicular to the dragged face
                # Try not to pick a dimension close to edge-on with the view vector
                vector = event.view.cameraVector.abs()
                dim = ((face.dimension + 1) % 2)
                if dim == vector.index(min(vector)):
                    dim = ((dim+1) % 2)

                self.dragResizeDimension = dim
                self.dragResizePosition = point[self.dragResizeDimension]
                self.oldBounds = self.bounds
                return True
            else:
                # Didn't hit - start new selection
                self.bounds = None

        # Get ready to start new selection
        if self.bounds is None:
            self.dragStartPoint = event.blockPosition
            self.dragStartFace = faces.FaceYIncreasing  # event.blockFace
            return

    def mouseMove(self, event):
        if self.dragStartPoint:
            # Show new box being dragged out
            newBox = self.boxFromDragSelect(event.ray)
            self.bounds = newBox

        if self.bounds is not None:
            if self.dragResizeFace is not None:
                # Hilite face being dragged
                newBox = self.boxFromDragResize(self.oldBounds, event.ray)
                self.faceDragNode.selectionBox = newBox
                self.faceDragNode.visible = True

                # Update selection box to resized size in progress
                newBox = self.boxFromDragResize(self.oldBounds, event.ray)
                self.bounds = newBox

            elif self.hiliteFace:
                # Hilite face cursor is over
                point, face = boxFaceUnderCursor(self.bounds, event.ray)
                if face is not None:
                    self.faceDragNode.visible = True
                    self.faceDragNode.face = face
                    self.faceDragNode.selectionBox = self.bounds
                else:
                    self.faceDragNode.visible = False

    def mouseRelease(self, event):
        if self.dragStartPoint:
            newBox = self.boxFromDragSelect(event.ray)
            self.dragStartPoint = None
            self.bounds = newBox
            self.boundsChangedDone.emit(newBox, True)

        elif self.dragResizeFace is not None:
            self.bounds = self.boxFromDragResize(self.oldBounds, event.ray)
            self.oldBounds = None
            self.dragResizeFace = None
            self.faceDragNode.visible = False
            self.boundsChangedDone.emit(self.bounds, False)


class SelectionCursorRenderNode(rendergraph.RenderNode):
    def drawSelf(self):
        point = self.sceneNode.point
        if point is None:
            return
        selectionColor = map(lambda a: a * a * a * a, self.sceneNode.color)
        r, g, b = selectionColor
        alpha = 0.3
        box = BoundingBox(point, (1, 1, 1))

        with gl.glPushAttrib(GL.GL_DEPTH_BUFFER_BIT | GL.GL_ENABLE_BIT):
            GL.glDepthMask(False)
            GL.glEnable(GL.GL_BLEND)
            GL.glPolygonOffset(DepthOffset.SelectionCursor, DepthOffset.SelectionCursor)

            # Wire box
            GL.glColor(1., 1., 1., alpha)
            cubes.drawBox(box, cubeType=GL.GL_LINES)

            # Highlighted face
            GL.glColor(r, g, b, alpha)
            cubes.drawFace(box, self.sceneNode.face)



class SelectionCursor(scenegraph.Node):
    RenderNodeClass = SelectionCursorRenderNode
    def __init__(self, point=Vector(0, 0, 0), face=faces.FaceXDecreasing, color=(1, .3, 1)):
        super(SelectionCursor, self).__init__()
        self._point = point
        self._face = face
        self._color = color

    @property
    def point(self):
        return self._point

    @point.setter
    def point(self, value):
        self._point = value
        self.dirty = True

    @property
    def face(self):
        return self._face

    @face.setter
    def face(self, value):
        self._face = value
        self.dirty = True

    @property
    def color(self):
        return self._color

    @color.setter
    def color(self, value):
        self._color = value
        self.dirty = True
