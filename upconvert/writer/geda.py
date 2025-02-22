#!/usr/bin/env python2
# pylint: disable=C0302
""" This module provides a writer class to generate valid gEDA
    file format data from a OpenJSON design. The module does
    not generate embedded symbols but writes each symbol to
    its own symbol file to reduce the amount of data in the
    schematic file. This also allows for reuse of system-wide
    gEDA symbol files.
    For gEDA to be able to find the generated symbols a project
    file *gafrc* will be placed in the same directory as the
    schematic file adding an instruction to include the local
    symbols directory. If a *gafrc* file already exists it will
    not be overwritten but a warning will be printed to check
    for the required instruction.

    An easy to use example to run the parser would be:
    >>> import writer.geda
    >>> writer.geda.GEDA(auto_include=True)
    >>> writer.geda.write(design, 'geda_test_design.sch')

    To provide additional symbol directories to use for
    symbol lookup try this:
    >>> import writer.geda
    >>> writer.geda.GEDA(symbol_dirs=[
        '/usr/share/gEDA/sym',
        'some/local/path/symbols',
    ])
    >>> writer.geda.write(design, 'geda_test_design.sch')
"""

# upconvert - A universal hardware design file format converter using
# Format:       upverter.com/resources/open-json-format/
# Development:  github.com/upverter/schematic-file-converter
#
# Copyright 2011 Upverter, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# Basic Strategy
# 0) converted file will be store in subdirectory
# 1) create subdirectory, symbol and project file
# 2) Write each component into a .sym file (even EMBEDDED components)
# 3) Write component instances to .sch file
# 4) Store net segments at the end of .sch file
#
# NOTE: The gEDA format is based on a 100x100 MILS grid where
# 1 MILS is equal to 1/1000 of an inch. In a vanilla gEDA file
# a blueprint-style frame is present with origin at
# (40'000, 40'000).


import os
import types
import codecs

from upconvert.core import shape
from upconvert.core import components
from upconvert.core.shape import Point
from upconvert.core.annotation import Annotation

from upconvert.parser import geda_commands
from upconvert.parser.geda import GEDAError
from upconvert.parser.geda import find_symbols


class GEDA:
    """ The gEDA Format Writer """

    ## gEDA grid size is 100mils
    ## to 10px in openjson format
    SCALE_FACTOR = 10

    ALIGNMENT = {
        'left': 0,
        'center': 3,
#        'right': 4,
        'right': 6,
    }

    def __init__(self, symbol_dirs=None):
        """ Constructs a new GEDA object and initialises it. *symbol_dirs*
            expects a list of directories. It will search for .sym files
            in all the specified directories.
        """
        print("==================================write GEDA==============================================")
        if symbol_dirs is None:
            symbol_dirs = []

        symbol_dirs = symbol_dirs + \
            [os.path.join(os.path.dirname(__file__), '..',
                          'library', 'geda')]
        print "symbol:",symbol_dirs

        self.known_symbols = find_symbols(symbol_dirs)
        ## offset used as bottom left origin as default
        ## in gEDA when starting new file
        self.offset = Point(0, 0)
        self.component_library = None

        ##NOTE: special attributes that are processed
        ## separately and will not be used as regular attributes
        self.ignored_attributes = [
            '_prefix',
            '_suffix',
            '_geda_imported',
        ]
        self.ignored_annotations = self.ignored_attributes + [
            'name',
            'refdes'
        ]
        self.project_dirs = {
            'symbol': None,
            'project': None,
        }

        self.component_names = []

    def set_offset(self, point):
        """ Set the offset point for the gEDA output. As OpenJSON
            positions the origin in the center of the viewport and
            gEDA usually uses (40'000, 40'000) as page origin, this
            allows for translating from one coordinate system to
            another. It expects a *point* object providing a *x* and
            *y* attribute.
        """
        self.offset.x = point.x
        self.offset.y = point.y

    def write(self, design, filename):
        """ Write the design to the gEDA format """
        self.component_library = dict()
        print "design_w",design
        attrs = design.design_attributes.attributes
        print ("-----------------" + str(attrs))
        self.offset.x = int(attrs.get('_geda_offset_x', 0))
        self.offset.y = int(attrs.get('_geda_offset_y', 0))

        ## setup project environment
        self.create_project_files(filename)
        print "creat_pr_file:",filename
        ## generate GEDA commands for all top-level shapes/pins
        ## in the current design
        output = self.generate_body_commands(design)
        print "generate_body_commands",output
        ## create symbol files for components writing all symbols
        ## to local 'symbols' directory. Symbols that are available
        ## in provided directories are ignored and referenced.
        a = design.components.components.items()
        print "aaaaaaaaaaaaaa----write_componen",a
        for library_id, component in design.components.components.items():
            print "library_id, component",library_id, component            
            self.write_component_to_file(library_id, component)   
        ## generate commands for schematic file from design
        ## output is a list of lines
        print "aaaaaaaaaaaaaaa-------write_sch"
        output += self.write_schematic_file(design)
        with codecs.open(filename, encoding='utf-8', mode='w') as f_out:
            f_out.write(self.commands_to_string(output))
        print "Done!!!"
    def create_project_files(self, filename):
        """ Creates various files and directories based on the *filename*.
            The directory of *filename* is assumed to be the project
            directory. The method creates a *gafrc* file adding support
            to load symbols from the local 'symbols' directory. If this
            directory does not exist it is created.
            The *gafrc* is not overwritten when it exists assume that there
            is more settings stored in it. A warning will be printed to
            stdout to remind you to add the directory lookup.
        """
        project_dir = os.path.dirname(filename)
        symbol_dir = os.path.join(project_dir, 'symbols-' + os.path.basename(filename))

        if not os.path.exists(symbol_dir):
            os.mkdir(symbol_dir)

        self.project_dirs['symbol'] = symbol_dir
        self.project_dirs['project'] = project_dir

    def write_schematic_file(self, design):
        """ Creates a list of gEDA commands based on the *design*.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        output = []

        ## create page frame & write name and owner
        output += self._create_schematic_title(design.design_attributes)
        ## create component instances
        print "gen_output_sch:",output
        output += self.generate_instances(design.component_instances)
        ## create gEDA commands for all nets
        print "genoutput_inst:",output
        output += self.generate_net_commands(design.nets)
        print "output:",output
        return output

    def generate_instances(self, component_instances):
        """ Generates a list of gEDA commands from the list of
            *component_instances*. For each instance the referenced
            component is retrieved and an attribute environment is
            attached if attributes are present for the given
            environment.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        commands = []
        for instance in component_instances:
            mirrored = 0
            print "instance:",instance
            if '_MIRRORED' in instance.library_id:
                mirrored = 1
            print "instance.library_id:",instance.library_id.replace('_MIRRORED', '')
            ## retrieve symbol for instance
            component_symbol = self.component_library[(
                instance.library_id.replace('_MIRRORED', ''),
                instance.symbol_index
            )]
            print "component_symbol:",component_symbol
            component_annotations = []
            ## create component instance for every symbolattribute
            attr_x, attr_y = 0, 0
            for symbol_attribute in instance.symbol_attributes: 
                print "symbol_attribute____",symbol_attribute
                if symbol_attribute.flip == True:
                    mirrored = 1 
                print "mirrored:",mirrored
                commands += self._create_component(
                    symbol_attribute.x,
                    symbol_attribute.y,
                    angle=symbol_attribute.rotation,
                    mirrored=mirrored,
                    basename=component_symbol,
                )
                print "command_gen_ins:",commands
                component_annotations += symbol_attribute.annotations
                attr_x = symbol_attribute.x
                attr_y = symbol_attribute.y

            for annotation in component_annotations:
                commands += self._convert_annotation(annotation)

            ## start an attribute environment
#            commands.append('{')
            a = []
            print "instance.attributes:",instance.attributes
            refdes = instance.attributes.get('name', None)
            refdes = instance.attributes.get('refdes', refdes)
            refdes = instance.attributes.get('id_inst', refdes)
            if refdes:
#                commands += self._create_attribute(
                a += self._create_attribute(
                    'refdes',
                    refdes,
                    attr_x,
                    attr_y,
                    visibility=1,
                )

            for key, value in instance.attributes.items():
                if key != 'refdes':
                    ## no position details available, stack attributes
                    attr_x, attr_y = attr_x + 10, attr_y + 10
#                    commands += self._create_attribute(
                    a += self._create_attribute(
                            key, value,
                            attr_x, attr_y
                    )

            ## close the attribute environment
#            commands.append('}')
            if len(a) != 0:
                commands.append('{')
                commands += a
                commands.append('}')
        return commands

    def write_component_to_file(self, library_id, component):
        """ Writes a *component* to a local symbol file and adds it to
            the symbol lookup used for instantiating components. A component
            might have a special attribute 'geda_imported' assigned when
            converted with the upconverter parser. This allows for retrieving
            that a local symbol file can be referenced. If this attribute is
            not present, a new symbol file will be generated in the project
            directory's symbols directory.
        """
        # pylint: disable=R0914

        ##NOTE: extract and remove gEDA internal attribute
        geda_imported = component.attributes.get('_geda_imported', 'false')
        geda_imported = (geda_imported == "true")

        prefix = component.attributes.get('_prefix', None)
        suffix = component.attributes.get('_suffix', None)
        print " prefix :",prefix,"suffix:",suffix
        if prefix is not None and suffix is not None:

            component.attributes['refdes'] = '%s?%s' % (prefix, suffix)

        symbol_filename = None
        ##NOTE: this attributed is used in the parser to mark at component
        ## as being imported using the upconverter. If this marker is found
        ## local .sym files will be referenced if available.
        if geda_imported:
            ##remove mirrored tag from name
            if '_MIRRORED' in component.name:
                component.name = component.name.replace('_MIRRORED', '')

            ##check if component is known sym file in OS dirs
            symbol_filename = "%s.sym" % component.name.replace('EMBEDDED', '')

            self.component_library[(
                library_id.replace('_MIRRORED', ''),
                0
            )] = symbol_filename

            if component.name.replace('EMBEDDED', '') in self.known_symbols:
                return

        ## symbol files should not use offset
        saved_offset = self.offset
        self.offset = shape.Point(0, 0)
#        print "component.attributes:",component.attributes
        ## write symbol file for each symbol in component
        for sym_idx, symbol in enumerate(component.symbols):
#            print "symbols.component",sym_idx, symbol
            symbol_attr = "_symbol_%d_0" % sym_idx
            if symbol_attr in component.attributes:
                prefix = component.attributes[symbol_attr]
                del component.attributes[symbol_attr]
            else:
                prefix = component.name
            if not geda_imported:
                prefix = prefix.replace(' ', '_')
                prefix_idx = 1
                while prefix in self.component_names:
                    prefix_idx += 1
                    prefix = prefix + str(prefix_idx)
                symbol_filename = "%s-%d.sym" % (prefix, sym_idx)
                if prefix not in self.component_names:
                    self.component_names.append(prefix)
            commands = []
            print "symbol_file----test:",symbol_filename
            for body in symbol.bodies:
                print "body:",body
                commands += self.generate_body_commands(body)
#                print "commands:",commands
            attr_y = 0
            for key, value in component.attributes.items():
                if not key.startswith('_symbol') \
                   and key not in self.ignored_attributes:
                    commands += self._create_attribute(
                        key, value,
                        0, attr_y,
                    )
                    attr_y = attr_y + 10

            ## write commands to file
            path = os.path.join(
                self.project_dirs['symbol'],
                symbol_filename
            )
            print "path:",path
            print "command_component:",commands
            with codecs.open(path, encoding='utf-8', mode='w') as fout:
                fout.write(self.commands_to_string(commands))

            ## required for instantiating components later
            self.component_library[(library_id, sym_idx)] = symbol_filename

        ## restore offset
        self.offset = saved_offset

    @staticmethod
    def commands_to_string(commands):
        """ Generates a string from the *commands* list. It assumes that each
            element in *commands* is a line, adding linebreaks accordingly.
            Required file headings are inserted. There resulting string is in
            valid gEDA file format and can be written to file directly.

            Returns gEDA fileformat string.
        """

        commands = ['v 20110115 2'] + commands
        return '\n'.join(commands)

    def generate_body_commands(self, body):
        """ Generates gEDA commands for *body* converting all shapes
            into valid gEDA shapes. If the body can be represented as
            a gEDA 'path' command it will generated as such. Pins are
            added after shapes.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        commands = []
        id_param = geda_commands.GEDAExtraParameter('id')
        ## extract paths
        paths = {}
        for shape_ in body.shapes:
            path_id = shape_.styles.get(id_param.name, None)
            paths.setdefault(path_id, []).append(shape_)   
        for shape_ in paths.get(None, []):
            method_name = '_convert_%s' % shape_.type
            print "shape_generate_body_commands:",shape_,method_name
            if hasattr(self, method_name):
                commands += getattr(self, method_name)(shape_)
                print "get_command_done",commands
            else:
                raise GEDAError(
                    "invalid shape '%s' in component" % shape_.type
                )
        print "cmd: ",commands
        for path_id in paths:
            if path_id is not None:
                commands += self._create_path(paths[path_id])

        ## create commands for pins
        print "body.pin",body.pins
        for pin_seq, pin in enumerate(body.pins):
            print "pin_seq, pin",pin_seq, pin
            commands += self._create_pin(pin_seq, pin)
 
#        print "return", commands
        return commands

    def generate_net_commands(self, nets):
        """ Generates gEDA commands for list of *nets*. Net names are
            retrieved from the 'name' attribute and are stored in the
            first gEDA net segment. By definition this will be populated
            in gEDA to all segments in the same net.

            Returns a list of gEDA commands without linebreaks.
        """
        commands = []

        for net in nets:

            ## check if 'name' attribute carries net name
            if 'name' in net.attributes and net.attributes['name']:
                net.attributes['netname'] = net.attributes['name']

            elif len(net.annotations) > 0:
                ## assume that the first annotation is net name
                annotation = net.annotations[0]
                net.attributes['netname'] = annotation.value
                net.annotations.remove(annotation)

            ## parse annotations into text commands
            for annotation in net.annotations:
                if annotation.value == 'name':
                    continue
                commands += self._create_text(
                    annotation.value,
                    annotation.x,
                    annotation.y,
                    rotation=annotation.rotation
                )

            ## generate list of segments from net points
            ## prevent segments from being added twice (reverse)
            segments = set()
            for start_id, start_pt in net.points.items():
                for end_id in start_pt.connected_points:
                    if (end_id, start_id) not in segments:
                        segments.add((start_id, end_id))

            attributes = dict(net.attributes)
            net_name = attributes.get('name', None)

            for segment in segments:
                start_id, end_id = segment

                ## check for missing points
                if start_id not in net.points or \
                        end_id not in net.points:
                    continue

                ## prevent zero-length segements from being written
                if net.points[start_id].x == net.points[end_id].x \
                    and net.points[start_id].y == net.points[end_id].y:
                    ## the same point is defined twice
                    continue

                commands += self._create_segment(
                    net.points[start_id],
                    net.points[end_id],
                    attributes=attributes,
                    net_name=net_name,
                )

                ## it's enough to store net name only in first element
                if attributes is not None:
                    attributes = None

        return commands

    def _create_schematic_title(self, design_attributes):
        """ Creates gEDA commands for the toplevel gEDA schematic
            including the schematic frame, title and owner name.
            Toplevel attributes are attached as well.

            Returns a list of gEDA commands without linebreaks.
        """
        commands = []
        print "design_attributes.attributes",design_attributes.annotations
        for i in design_attributes.annotations:
            print "i:",i
        title_frame = design_attributes.attributes.pop(
            '_geda_titleframe',
            None,
        )
        print "title_frame:",title_frame
        if title_frame is None:
            return []

        commands += self._create_component(
            0, 0,  # use 0, 0 as coordinates will be converted in component
            title_frame + '.sym',
        )

        if design_attributes.metadata.owner:
            commands += self._create_text(
                design_attributes.metadata.owner,
                1390,
                10,
                size=10
            )

        if design_attributes.metadata.name:
            commands += self._create_text(
                design_attributes.metadata.name,
                1010,
                80,
                size=20
            )

        if design_attributes.metadata.license:
            commands += self._create_attribute(
                '_use_license',
                design_attributes.metadata.license,
                0, 0
            )

        ## set coordinates at offset for design attributes
        attr_x, attr_y = 0, 0
        for key, value in design_attributes.attributes.items():
            if key.startswith('_geda'):
                continue
            commands += self._create_attribute(
                key, value,
                attr_x,
                attr_y,
            )
            attr_y = attr_y + 10

        return commands

#    def _create_component(self, x, y, basename, angle=0, mirrored=0):
    def _create_component(self, x, y, basename, angle, mirrored):
        """ Creates a gEDA command for a component in symbol file *basename*
            at location *x*, *y*. *angle* allows for specifying the rotation
            angle of the component and is specified in pi radians. Valid values
            are 0.0, 0.5, 1.0, 1.5.

            Returns a list of gEDA commands without linebreaks.
        """
        # pylint: disable=C0103,R0913
        print  x, y, basename, angle, mirrored
        x, y = self.conv_coords(x, y)
        return geda_commands.GEDAComponentCommand().generate_command(
            x=x, y=y,
            angle=self.conv_angle(angle),
            mirror=mirrored,
            basename=basename
        )

    def _create_attribute(self, key, value, x, y, **kwargs):
        """ Creates a gEDA attribute command from *key* and *value*
            at position *x*,*y*. If *key* is prefixed by '_' it is
            interpreted as private and the attribute will be set as
            invisible. Visibility can be specified explicitly using
            the keyword *visibility*.

            Returns a list of gEDA commands without linebreaks.
        """
        # pylint: disable=C0103
        if key in self.ignored_attributes or not value:
            return []
        ## make private attribute invisible in gEDA
        if key.startswith('_'):
            key = key[1:]
            kwargs['visibility'] = 0
        try:
            text = "%s=%s" % (unicode(key), unicode(value))
        except UnicodeDecodeError:
            text = "%s=%s" % (unicode(key), unicode(value, errors='replace'))
        kwargs['style_color'] = geda_commands.GEDAColor.ATTRIBUTE_COLOR
        print "kwargs__create_attribute:",kwargs
        return self._create_text(text, x, y, **kwargs)

    def _create_text(self, text, x, y,size='10', **kwargs):
        """ Creates a gEDA text command with *text* at position
            *x*, *y*. Further valid keywords include *size*,
            *alignment*, *angle* and *visibility*.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        # pylint: disable=C0103
        if isinstance(text, basestring):
            text = text.split('\n')
        assert(isinstance(text, types.ListType))

        kwargs.update({
            'x': self.x_to_mils(x),
            'y': self.y_to_mils(y),
            'angle': self.conv_angle(kwargs.get('angle', 0)),
#            'angle': self.conv_angle(kwargs.get('angle', 180)),
#            'alignment': self.ALIGNMENT[kwargs.get('alignment', 'left')],
            'alignment': self.ALIGNMENT[kwargs.get('alignment', 'right')],
            'num_lines': len(text),
            'size': size,  #  follow size viewdraw but not use
        })
        commands = geda_commands.GEDATextCommand().generate_command(**kwargs)
        return commands + text

    def _create_pin(self, pin_seq, pin):
        """ Creates a pin command followed by the mandatory
            attribute environment. The numeric *pin_seq*
            is stored as gEDA attribute *pinseq*. *pinnummer*
            attribute is taken from the pin's pin_number
            attribute. If the pin has a label it will a
            *pinlabel* gEDA attribute is attached.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        # pylint: disable=W0142
        assert(issubclass(pin.__class__, components.Pin))

        connected_x, connected_y = pin.p2.x, pin.p2.y
        kwargs = {
            "x1": self.x_to_mils(connected_x),
            "y1": self.y_to_mils(connected_y),
            "x2": self.x_to_mils(pin.p1.x),
            "y2": self.y_to_mils(pin.p1.y),
        }
        kwargs.update(pin.styles)
        command = geda_commands.GEDAPinCommand().generate_command(**kwargs)
        command.append('{')
        if pin.label is not None:
            attribute = self._create_attribute(
                'pinlabel',
                pin.label.text,
                pin.label.x,
                pin.label.y,
                alignment=pin.label.align,
                angle=pin.label.rotation
            )
            command += attribute   
        command += self._create_attribute(
            'pinseq',
            pin_seq,
            connected_x + 1,    #origrin 10
            connected_y + 1,    #origrin 10
            visibility=0,
        )
        print "command += self._create_attribute(",command
        command += self._create_attribute(
            'pinnumber',
            pin.pin_number,
            connected_x + 1,      #origrin 10
            connected_y + 2,      #origrin 20
            visibility=0,
        )
        print "command += self._create_attribute( 2"
        command.append('}')       
        return command

    def _convert_annotation(self, annotation):
        """ Converts Annotation object in *annotation* into a
            gEDA text command. If the annotation text is
            enclosed in '{{' '}}' it will be ignored and an
            empty list is returned.

            Returns a list of gEDA commands without linebreaks.
        """
        # pylint: disable=W0142
        assert(issubclass(annotation.__class__, Annotation))

        if annotation.value in self.ignored_annotations:
            return []

        if bool(annotation.visible in (True, 'true')):
            visibility = 1
        else:
            visibility = 0

        kwargs = dict(
            text=annotation.value,
            x=annotation.x,
            y=annotation.y,
            angle=annotation.rotation,
            visibility=visibility,
        )
        return self._create_text(**kwargs)

    def _convert_arc(self, arc):
        """ Converts Arc object in *arc* into a gEDA arc command.
            Returns a list of gEDA commands without line breaks.
        """
        # pylint: disable=C0103,W0142
        assert(issubclass(arc.__class__, shape.Arc))

        x, y = self.conv_coords(arc.x, arc.y)
        start_angle = self.conv_angle(arc.start_angle)

        sweep_angle = self.conv_angle(arc.end_angle) - start_angle
        if sweep_angle < 0:
            sweep_angle = 360 + sweep_angle

        kwargs = dict(
            x=x, y=y,
            radius=self.to_mils(arc.radius),
            startangle=start_angle,
            sweepangle=sweep_angle,
        )
        kwargs.update(arc.styles)
        return geda_commands.GEDAArcCommand().generate_command(**kwargs)

    def _convert_circle(self, circle):
        """ Converts Circle object in *circle* to gEDA circle command.

            Returns gEDA command as list without trailing line breaks.
        """
        # pylint: disable=W0142
        assert(issubclass(circle.__class__, shape.Circle))

        center_x, center_y = self.conv_coords(circle.x, circle.y)
        kwargs = dict(
            x=center_x,
            y=center_y,
            radius=self.to_mils(circle.radius)
        )
        kwargs.update(circle.styles)
        return geda_commands.GEDACircleCommand().generate_command(**kwargs)

    def _convert_rounded_rectangle(self, rect):
        """ Converts RoundedRectangle object into gEDA rectangle command.

            Returns gEDA command (without trailing line break) as list.
        """
        return self._convert_rectangle(rect)

    def _convert_rectangle(self, rect):
        """ Converts Rectangle object into gEDA rectangle command.

            Returns gEDA command (without trailing line break) as list.
        """
        # pylint: disable=W0142
        assert(issubclass(rect.__class__, (shape.Rectangle,
                                           shape.RoundedRectangle)))

        top_x, top_y = self.conv_coords(rect.x, rect.y)
        width, height = self.to_mils(rect.width), self.to_mils(rect.height)
        kwargs = dict(
            x=top_x,
            y=(top_y - height),
            width=width,
            height=height
        )
        kwargs.update(rect.styles)
        return geda_commands.GEDABoxCommand().generate_command(**kwargs)

    def _convert_line(self, line):
        """ Converts Line object in *line* to gEDA command.

            Returns gEDA command (without line break) as list.
        """
        # pylint: disable=W0142
        assert(issubclass(line.__class__, shape.Line))

        start_x, start_y = self.conv_coords(line.p1.x, line.p1.y)
        end_x, end_y = self.conv_coords(line.p2.x, line.p2.y)

        kwargs = dict(
            x1=start_x,
            y1=start_y,
            x2=end_x,
            y2=end_y
        )
        kwargs.update(line.styles)
        return geda_commands.GEDALineCommand().generate_command(**kwargs)

    def _convert_label(self, label):
        """ Converts Label object in *label* to gEDA command.
            Returns gEDA command (without line break) as list.
        """
        # pylint: disable=W0142
#        print "label",label
        assert(issubclass(label.__class__, shape.Label))
#        print "label:",label.text
#        print "x:",label.x
#        print "y:",label.y
#        print "alignment:",label.align
#        print "angle:",label.rotation
        
        kwargs = dict(
            text=label.text,
            x=label.x,
            y=label.y,
            alignment=label.align,
            angle=label.rotation,
#            size=label.font_size,   # use size follow viewdraw
        )
        kwargs.update(label.styles)
        return self._create_text(**kwargs)

    def _create_segment(self, np1, np2, attributes=None, net_name=None):
        """ Creates net segment from NetPoint *np1* to
            *np2*. If dictionary of *attributes* is specified
            commands for the attribute environment are generated
            as well.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        np1_x, np1_y = self.conv_coords(np1.x, np1.y)
        np2_x, np2_y = self.conv_coords(np2.x, np2.y)

        command = geda_commands.GEDASegmentCommand().generate_command(
            x1=np1_x, y1=np1_y,
            x2=np2_x, y2=np2_y,
        )

        if attributes is None:
            attributes = {}

        if net_name:
            attributes['netname'] = net_name

        if attributes:
            command.append('{')
            for key, value in attributes.items():
                command += self._create_attribute(
                    key,
                    value,
                    np1.x + 10,
                    np1.y + 10
                )
            command.append('}')

        return command

    def _convert_polygon(self, polygon):
        """ Converts Polygon object in *polygon* to gEDA path command.

            Returns a list of gEDA commands without trailing linebreaks.
        """
        num_lines = len(polygon.points) + 1  # add closing command to polygon
        commands = geda_commands.GEDAPathCommand().generate_command(
            num_lines=num_lines
        )

        start_x, start_y = polygon.points[0].x, polygon.points[0].y
        commands.append('M %d,%d' % self.conv_coords(start_x, start_y))

        for point in polygon.points[1:]:
            commands.append('L %d,%d' % self.conv_coords(point.x, point.y))

        commands.append('z')  # closes the polygon

        return commands

    def _create_path(self, shapes):
        """ Creates a set of gEDA commands for *path*.

            Returns gEDA commands without trailing linebreaks as list.
        """
        # pylint: disable=R0914,W0142
        num_lines = 1
        shapes = list(shapes)  # create new list to be able to modify

        current_x, current_y = self.conv_coords(
            shapes[0].p1.x,
            shapes[0].p1.y
        )
        start_x, start_y = current_x, current_y
        command = ['M %d,%d' % (start_x, start_y)]

        close_command = []
        ## check if last element is line back to starting point
        if shapes[-1].type == 'line':
            if shapes[-1].p2.x == shapes[0].p1.x \
                and shapes[-1].p2.y == shapes[0].p1.y:
                ## discard line and close path instead
                close_command = ['z']
                shapes.remove(shapes[-1])
                num_lines += 1

        shape_styles = {}  # store shape style in this dict for later use
        for shape_obj in shapes:
            if shape_obj.type == 'line':
                current_x, current_y = self.conv_coords(
                    shape_obj.p2.x,
                    shape_obj.p2.y
                )
                command.append("L %d,%d" % (current_x, current_y))

            elif shape_obj.type == 'bezier':
                c1_x, c1_y = self.conv_coords(
                    shape_obj.control1.x,
                    shape_obj.control1.y
                )
                c2_x, c2_y = self.conv_coords(
                    shape_obj.control2.x,
                    shape_obj.control2.y
                )
                current_x, current_y = self.conv_coords(
                    shape_obj.p2.x,
                    shape_obj.p2.y
                )

                command += [
                    'C %d,%d %d,%d %d,%d' % (
                        c1_x, c1_y,
                        c2_x, c2_y,
                        current_x, current_y
                    )
                ]

            else:
                raise GEDAError(
                    "shape type '%s' invalid in path" % shape_obj.type
                )

            num_lines += 1
            shape_styles.update(shape_obj.styles)

        kwargs = {
            'num_lines': num_lines,
        }
        kwargs.update(shape_styles)
        return geda_commands.GEDAPathCommand().generate_command(
            **kwargs
        ) + command + close_command

    def _convert_bezier(self, curve):
        """ Converts BezierCurve object in *curve* to gEDA curve command.

            Returns gEDA command without trailing linebreaks as list.
        """
        # pylint: disable=W0142
        assert(issubclass(curve.__class__, shape.BezierCurve))
        p1_x, p1_y = self.conv_coords(curve.p1.x, curve.p1.y)
        c1_x, c1_y = self.conv_coords(curve.control1.x, curve.control1.y)

        p2_x, p2_y = self.conv_coords(curve.p2.x, curve.p2.y)
        c2_x, c2_y = self.conv_coords(curve.control2.x, curve.control2.y)

        path_commands = [
            'M %d,%d' % (p1_x, p1_y),
            'C %d,%d %d,%d %d,%d' % (c1_x, c1_y, c2_x, c2_y, p2_x, p2_y)
        ]

        kwargs = {
            'num_lines': len(path_commands),
        }
        kwargs.update(curve.styles)
        return geda_commands.GEDAPathCommand().generate_command(
            **kwargs
        ) + path_commands

    @staticmethod
    def is_valid_path(body):
        """ Checks if *body* contains only shapes that can be
            represented as a gEDA path. If body contains only
            Line and BezierCurve shapes and all shapes are
            succesively connected it is a valid path.

            Returns True for body that can be represented as gEDA
                path, False otherwise.
        """
        current_pt = None

        for shape_obj in body.shapes:
            if shape_obj.type not in ['line', 'bezier']:
                return False

            if current_pt is None:
                current_pt = shape_obj.p1

            if not (current_pt.x == shape_obj.p1.x
                and current_pt.y == shape_obj.p1.y):
                return False

            current_pt = shape_obj.p2

        return True

    def to_mils(self, value):
        """ Converts *value* from px to mils based on the
            scaling factor. Offset is not used.

            Returns integer value in MILS.
        """
        return value * self.SCALE_FACTOR

    def y_to_mils(self, y_px):
        """ Converts *y_px* from pixel to mils and translating
            it along the Y axis according to the offset value.

            Returns a scaled and translated Y coordinate in MILS.
        """
        value = (y_px * self.SCALE_FACTOR) + self.offset.y
        return value

    def x_to_mils(self, x_px):
        """ Converts *x_px* from pixel to mils and translating
            it along the X axis according to the offset value.

            Returns a scaled and translated X coordinate in MILS.
        """
        value = (x_px * self.SCALE_FACTOR) + self.offset.x
        return value

    @staticmethod
    def conv_angle(angle, steps=1):
        """ Converts *angle* in pi radians into degrees. If
            *steps* is set, it will be used to limit angles
            to the provide steps in degrees.

            Retuns converted and cut-off angle in degrees.
        """
#        converted_angle = int(angle * 180) // int(steps)
        converted_angle = int(angle * 90) // int(steps)
        converted_angle *= steps

        ## convert from clockwise rotation to counter-clockwise
        ## as used in gEDA schematic
        return converted_angle

    def conv_coords(self, x_px, y_px):
        """ Converts *x_px*, *y_px* from pixel to mils and translating
            it along the X- and Y-axes, respectively, according to
            the offset value.

            Returns a scaled and translated coordinates in MILS.
        """
        return (
            self.x_to_mils(x_px),
            self.y_to_mils(y_px)
        )
