#! /usr/bin/env python

"""
This standalone python script can be used to convert the force-fields in MSI
format (.FRC files, a.k.a. "BIOSYM", "DISCOVERY" format)
...into MOLTEMPLATE/LAMMPS format (.LT format).

Once converted into moltemplate (.LT) format, users can use these files with 
MOLTEMPLATE to prepare LAMMPS simulations of molecules using these force fields 
(without needing any additional software such as msi2lmp).

There are several examples of MSI files in the "tools/msi2lmp/frc_files/"
directory which is distributed with LAMMPS.

Limitations:

Currently (2017-2) this script ignores the "template" information in .FRC files.
When defining a new type of molecule, the user must carefully choose the
complete atom type for each type of atom in the molecule.  In other words,
MOLTEMPLATE will not attempt to determine (from local context) whether 
a carbon atom somewhere in your molecule happens to be an SP3 carbon 
(ie. "c4" in the COMPASS force-field), or an aromatic carbon ("c3a"), 
or something else (for example).  This information is typically contained 
in the "templates" section of these files, and this script currently ignores
that information.  Instead, the user must determine which type of carbon atom
it is manually, for all of the carbon atoms in that kind of molecule.
(This only needs to be done once per molecule definition.
 Once a type of molecule is defined, it can be copied indefinitely.)

"""


__author__ = 'Andrew Jewett'
__version__ = '0.1.0'
__date__ = '2017-2-07'


import sys
import os
from sets import Set
from collections import defaultdict, OrderedDict
from operator import itemgetter
from math import *

g_program_name = __file__.split('/')[-1]


doc_msg = \
    "Typical Usage:\n\n" + \
    "   " + g_program_name + " -name COMPASS < compass_published.frc > compass.lt\n\n" + \
    "   where \"compass_published.frc\" is a force-field file in MSI format.\n" + \
    "         \"comass.lt\" is the corresponding file converted to moltemplate format\n" + \
    "   and   \"COMPASS\" is the name that future moltemplate users will use to refer\n" + \
    "         to this force-field (optional).\n" + \
    "Optional Arguments\n" + \
    "   -name FORCEFIELDNAME # Give the force-field a name\n" + \
    "   -file FILE_NAME      # Read force field parameters from a file\n" + \
    "   -url URL             # Read force field parameters from a file on the web\n" + \
    "   -atoms \"QUOTED LIST\" # Restrict output to a subset of atom types\n" + \
    "   -auto                # Consider auto_equivalences in the .frc file \n" + \
    "  Sometimes an FRC file contains multiple versions.  In that case,\n"+\
    "  you can select between them using these optional arguments:\n"+\
    "   -pair-style \"PAIRSTYLE ARGS\" # LAMMPS pair style and cutoff arg(s)\n" + \
    "   -bond-style BONDSTYLE  # desired LAMMPS bond style (default: \"class2\")\n" + \
    "   -angle-style ANGLESTYLE  # desired LAMMPS angle style\n" + \
    "   -dihedral-style DIHEDRALSTYLE  # desired LAMMPS dihedral style\n" + \
    "   -improper-style IMPROPERSTYLE  # desired LAMMPS improper style\n"
    #"   -hbond-style \"HBONDTYLE ARGS\" # LAMMPS hydrogen-bond style and args\n"



def NSplitQuotedString(string,
                       nmax=0,
                       quotes='\'\"',
                       delimiters=' \t\r\f\n',
                       escape='\\',
                       comment_char='#'):
    """
    Split a quoted & commented string into at most "nmax" tokens (if nmax>0),
    where each token is separated by one or more delimeter characters
    in the origingal string, and quoted substrings are not split,
    This function returns a list of strings.  Once the string is split Nmax
    times, any remaining text will be appended to the last entry of the list.
    Comments are stripped from the string before splitting begins.
    """
    tokens = []
    token = ''
    reading_token = True
    escaped_state = False
    quote_state = None
    for c in string:

        if (c in comment_char) and (not escaped_state) and (quote_state == None):
            tokens.append(token)
            return tokens

        elif (c in delimiters) and (not escaped_state) and (quote_state == None):
            if reading_token:
                if (nmax > 0) and (len(tokens) < nmax-1):
                    tokens.append(token)
                    token = ''
                    reading_token = False
                else:
                    token += c

        elif c in escape:
            if escaped_state:
                token += c
                reading_token = True
                escaped_state = False
            else:
                escaped_state = True
                # and leave c (the '\' character) out of token
        elif (c in quotes) and (not escaped_state):
            if (quote_state != None):
                if (c == quote_state):
                    quote_state = None
            else:
                quote_state = c
            token += c
            reading_token = True
        else:
            if (c == 'n') and (escaped_state == True):
                c = '\n'
            elif (c == 't') and (escaped_state == True):
                c = '\t'
            elif (c == 'r') and (escaped_state == True):
                c = '\r'
            elif (c == 'f') and (escaped_state == True):
                c = '\f'
            token += c
            reading_token = True
            escaped_state = False

    if len(string) > 0:
        tokens.append(token)
    return tokens




def SplitQuotedString(string,
                      quotes='\'\"',
                      delimiters=' \t\r\f\n',
                      escape='\\',
                      comment_char='#'):

    return NSplitQuotedString(string, 0,
                              quotes, delimiters, escape, comment_char)




def RemoveOuterQuotes(text, quotes='\"\''):
    if ((len(text) >= 2) and (text[0] in quotes) and (text[-1] == text[0])):
        return text[1:-1]
    else:
        return text


def ReverseIfEnds(l_orig):
    """
    Convenient to have a one-line macro for swapping list order if first>last
    """
    l = [x for x in l_orig]
    if l[0] > l[-1]:
        l.reverse()
    return l



#def Repl(tokens, a, b):
#    return [(b if x==a else x) for x in tokens]


def EncodeAName(s):
    """
    Handle * characters in MSI atom names
    """

    # If the atom name begins with *, then it is a wildcard
    if s[:1] == '*': # special case: deal with strings like  *7
        return 'X'   # These have special meaning.  Throw away the integer.
                     # (and replace the * with an X)

    # If the * character occurs later on in the atom name, then it is actually
    # part of the atom's name.  (MSI force fields use many strange characters in
    # atom names.)  Here we change the * to \* to prevent the atom name from
    # being interpreted as a wild card in the rules for generating bonds,
    # angles, dihedrals, and impropers.
    
    return s.replace('*','\\*')  # this prevents ttree_lex.MatchesAll()
                                 # from interpreting the '*' as a wildcard

    # alternately:
    #return s.replace('*','star') # '*' is reserved for wildcards in moltemplate
    #                             # 'star' is a string that is unused in any 
    #                             # of the force fields I have seen so far.
    

def DeterminePriority(anames,
                      is_auto):
    """
    scan through list of strings anames, looking for patterns of the form
    *n
    where n is an integer.
    (These patterns are used by MSI software when using "auto_equivalences"
     to look up force field parameters for bonded interactions.)
    Make sure this pattern only appears once and return n to the caller.

    (It's annoying: I can't be sure whether the syntax for "auto" interactions 
     is slightly different than it is for ordinary bonded interactions,
     So I formally require this information ("is_auto") in case it matters
     one day.)
    """
    n = None
    for i in range(0, len(anames)):
        if anames[:1] == a:
            if n == None:
                n = int(anames[1:])
            elif n != int(anames[1:]):
                raise Exception('Error: Inconsistent priority integers in the following interaction:\n'
                                '      ' + ' '.join(anames) + '\n')
    if n == None:
        return 0  # no priority numbers found. return default (0)
    return n

def IsAutoInteraction(interaction_name)
    return interaction_name.find('auto') == 0

def EncodeInteractionName(anames, is_auto):
    if is_auto:
        priority = DeterminePriority(anames, is_auto):
        return 'auto' + str(priority)+','.join(anames)
    return ','.join(anames)

def ExtractANames(interaction_name):
    if IsAutoInteraction(interaction_name):
        return interaction_name[5:].split(',')
    return interaction_name.split(',')


def OOPImproperNameSort(aorig):
    assert(len(aorig) == 4)
    atom_names = map(EncodeAName, aorig)
    if atom_names[0] < atom_names[3]:
        return (atom_names, [0:4])
    else:
        return ([atom_names[3],
                 atom_names[1],
                 atom_names[2],
                 atom_names[0]],
                [3,1,2,0])


def Class2ImproperNameSort(aorig):
    """
    This function takes a list of 4 strings as an argument representing 4 atom
    names for atoms participating in an "improper" ("wilson-out-of-plane")
    interaction.  This function assumes the second atom is the central ("hub") 
    atom in the interaction, and it sorts the remaining atoms names.
    This function also replaces any occurence of \"*\" with \"X\".
    The new list is returned to the caller, along with the permutation.
    """
    assert(len(aorig) == 4)
    atom_names = map(EncodeAName, aorig)
    z = zip([atom_names[0], atom_names[2], atom_names[3]],
            [0,2,3])
    z.sort()
    l = [z[0][0], atom_names[1], z[2][0], z[3][0]]
    p = [z[0][1], 1, z[2][1], z[3][1]]
    return (l,p)


def ImCrossTermIDs(atom_names):
    if atom_names[0] <= atom_names[3]:
        cross_name = (atom_name[0]+','+atom_name[1]+','+
                      atom_name[2]+','+atom_name[3])



def Equivalences2ffids(lines_equivalences,
                       atom_types):
    """
    This function reads a list of lines containing "equivalences" and
    "auto_equivalences" from an MSI-formatted .FRC file.
    Then, for each atom type, it generates a long string which includes the 
    original atom type name as well as all of the equivalences it belongs to.
    Later on, when it is time to generate angles, dihedrals, or impropers,
    moltemplate will search for patterns contained in these strings to decide
    which type of interaction to generate.
    This function returns a dictionary that converts the original atom type name
    into these strings.
    """
    atom2equiv_pair = OrderedDict()
    atom2equiv_bond = OrderedDict()
    atom2equiv_angle = OrderedDict()
    atom2equiv_dihedral = OrderedDict()
    atom2equiv_improper = OrderedDict()
    for line in lines_equivalences:
        tokens = SplitQuotedString(line.strip(),
                                   comment_char='!>'))
        atype = tokens[2]
        atom2equiv_pair[atype] = tokens[3]
        atom2equiv_bond[atype] = tokens[4]
        atom2equiv_angle[atype] = tokens[5]
        atom2equiv_dihedral[atype] = tokens[6]
        atom2equiv_improper[atype] = tokens[7]

    atom2ffid = OrderedDict()
    for atom in atom_types:
        atom2ffid[atom] = (atom + 
                           #',p'+atom2equiv_pair.get(atom,'') + 
                           ',b'+atom2equiv_bond.get(atom,'') + 
                           ',a'+atom2equiv_angle.get(atom,'') + 
                           ',d'+atom2equiv_dihedral.get(atom,'') + 
                           ',i'+atom2equiv_improper.get(atom,''))
    return atom2ffid





def AutoEquivalences2ffids(lines_equivalences,
                           lines_auto_equivalences,
                           atom_types):
    """
    This function is a variant of Equivalences2ffids() which also considers
    "auto_equivalences".
    This function returns a dictionary that converts the original atom type name
    into a string that includes that atom's "equivalences",
    as well as its "auto_equivalences".
    moltemplate will search for patterns contained in these strings to decide
    which type of interaction to generate.
    """
    atom2equiv_pair = OrderedDict()
    atom2equiv_bond = OrderedDict()
    atom2equiv_angle = OrderedDict()
    atom2equiv_dihedral = OrderedDict()
    atom2equiv_improper = OrderedDict()
    for line in lines_equivalences:
        tokens = SplitQuotedString(line.strip(),
                                   comment_char='!>'))
        atype = tokens[2]
        atom2equiv_pair[atype] = tokens[3]
        atom2equiv_bond[atype] = tokens[4]
        atom2equiv_angle[atype] = tokens[5]
        atom2equiv_dihedral[atype] = tokens[6]
        atom2equiv_improper[atype] = tokens[7]


    # ------ The following lines are for processing "auto_equivalences" -----
    #
    # What is the difference between "equivalences" and "auto_equivalences"?
    #
    # equivalences:
    # Here is an excerpt from the Discover manual describing "equivalences":
    #  "Chemically distinct atoms often differ in some, but not all,
    #   of their forcefield parameters. For example, the bond parameters
    #  for the C-C bonds in ethene and in benzene are quite different,
    #  but the nonbond parameters for the carbon atoms are essentially
    #  the same. Rather than duplicating the nonbond parameters in the
    #  forcefield parameter file, the Discover program uses atom type
    #  equivalences to simplify the problem. In the example, the phenyl
    #  carbon atom type is equivalent to the pure sp2 carbons of ethene
    #  insofar as the nonbond parameters are concerned. The Discover
    #  program recognizes five types of equivalences for each atom
    #  type: nonbond, bond, angle, torsion, and out-of-plane.
    #  Cross terms such as bond-bond terms have the same equivalences
    #  (insofar as atom types are concerned) as the diagonal term of
    #  the topology of all the atoms defining the internal coordinates.
    #  For the bond-bond term, this means that the atom type
    #  equivalences for angles would be used
    #
    # auto_equivalences:
    #   Are similar to equivalences, but apparently with lower priority.
    #   In addition, it seems that, when looking up some of the class2 terms
    #   in the interaction according to atom type using "auto_equivalences"
    #   a distinction is made between end atoms and central atoms.
    #   The parameters for these interactions are also stored in different 
    #   tables in the .frc file, with different comments/tags.
    #   (for example, "cff91_auto" as opposed to "cff91")
    # An excerpt from the Discover manual is somewhat vague:
    #  "A forcefield may include automatic parameters for use when
    #   better-quality explicit parameters are not defined for a
    #   particular bond, angle, torsion, or out-of-plane interaction.
    #   These parameters are intended as temporary patches, to allow
    #   you to begin calculations immediately."

    atom2auto_e_pair = OrderedDict()
    atom2auto_e_bondincr = OrderedDict()
    atom2auto_e_bond = OrderedDict()
    atom2auto_e_angleend = OrderedDict()
    atom2auto_e_anglecenter = OrderedDict()
    atom2auto_e_dihedralend = OrderedDict()
    atom2auto_e_dihedralcenter = OrderedDict()
    atom2auto_e_improperend = OrderedDict()
    atom2auto_e_impropercenter = OrderedDict()
    for line in lines_auto_equivalences:
        tokens = SplitQuotedString(line.strip(),
                                   comment_char='!>'))
        atype = tokens[2]
        atom2auto_e_pair = tokens[3]
        atom2auto_e_bondincr = tokens[4]
        atom2auto_e_bond = tokens[5]
        atom2auto_e_angleend[atype] = tokens[6]
        atom2auto_e_anglecenter[atype] = tokens[7]
        atom2auto_e_dihedralend[atype] = tokens[8]
        atom2auto_e_dihedralcenter[atype] = tokens[9]
        atom2auto_e_improperend[atype] = tokens[10]
        atom2auto_e_impropercenter[atype] = tokens[11]

    atom2ffid = OrderedDict()
    for atom in atom_types:
        atom2ffid[atom] = (atom + 
                           #',p'+atom2equiv_pair.get(atom,'') + 
                           ',b'+atom2equiv_bond.get(atom,'') + 
                           ',a'+atom2equiv_angle.get(atom,'') + 
                           ',d'+atom2equiv_dihedral.get(atom,'') + 
                           ',i'+atom2equiv_improper.get(atom,'') + 
                           #',ap'+atom2auto_e_pair.get(atom,'') + 
                           ',aq'+atom2auto_e_bondincr.get(atom,'') + 
                           ',ab'+atom2auto_e_bond.get(atom,'') + 
                           ',aae'+atom2auto_e_angleend.get(atom,'') + 
                           ',aac'+atom2auto_e_anglecenter.get(atom,'') + 
                           ',ade'+atom2auto_e_dihedralend.get(atom,'') + 
                           ',adc'+atom2auto_e_dihedralcenter.get(atom,'') + 
                           ',aie'+atom2auto_e_improperend.get(atom,'') + 
                           ',aic'+atom2auto_e_impropercenter.get(atom,''))
    return atom2ffid






def main():
    try:
        sys.stderr.write(g_program_name + ", version " +
                         __version__ + ", " + __date__ + "\n")
        if sys.version < '2.6':
            raise Exception('Error: Using python ' + sys.version + '\n' +
                            '       Alas, your version of python is too old.\n'
                            '       You must upgrade to a newer version of python (2.6 or later).')
    
        if sys.version < '2.7':
            from ordereddict import OrderedDict
        else:
            from collections import OrderedDict 
    
        if sys.version > '3':
            import io
        else:
            import cStringIO
    
        # defaults:
        ffname = 'BIOSYM_MSI_FORCE_FIELD'
        type_subset = Set([])
        filename_in = ''
        file_in = sys.stdin
        include_auto_equivalences = False
        #kspace_style = 'kspace_style pppm 0.0001'
        #pair_style_name = 'lj/class2/coul/long'
        #pair_style_params = "10.0 10.0"
        kspace_style = ''

        pair_style2docs = {}
        pair_style2args = defaultdict(str)
        pair_style2docs['lj/cut/long'] = 'http://lammps.sandia.gov/doc/pair_lj.html'
        pair_style2args['lj/cut/long'] = '10.5'
        pair_style2docs['class2'] = 'http://lammps.sandia.gov/doc/pair_class2.html'

        bond_style2docs = {}
        bond_style2args = defaultdict(str)
        bond_style2docs['harmonic'] = 'http://lammps.sandia.gov/doc/bond_harmonic.html'
        bond_style2docs['class2'] = 'http://lammps.sandia.gov/doc/bond_class2.html'
        bond_style2docs['morse'] = 'http://lammps.sandia.gov/doc/bond_morse.html'

        angle_style2docs = {}
        angle_style2args = defaultdict(str)
        angle_style2docs['harmonic'] = 'http://lammps.sandia.gov/doc/angle_harmonic.html'
        angle_style2docs['class2'] = 'http://lammps.sandia.gov/doc/angle_class2.html'

        dihedral_style2docs = {}
        dihedral_style2args = defaultdict(str)
        dihedral_style2docs['charmm'] = 'http://lammps.sandia.gov/doc/dihedral_charmm.html'
        dihedral_style2docs['class2'] = 'http://lammps.sandia.gov/doc/dihedral_class2.html'
        # CONTINUEHERE: do we still need the following variable?
        dihedral_symmetry_subgraph = ''  # default

        improper_style2docs = {}
        dihedral_style2args = defaultdict(str)
        improper_style2docs['cvff'] = 'http://lammps.sandia.gov/doc/improper_cvff.html'
        improper_style2docs['class2'] = 'http://lammps.sandia.gov/doc/improper_class2.html'
        # CONTINUEHERE: do we still need the following variable?
        improper_symmetry_subgraph = 'cenJsortIKL'


        special_bonds_command = 'special_bonds lj/coul 0.0 0.0 1.0 dihedral yes'
        pair_mixing_style = 'sixthpower tail yes'
        contains_united_atoms = False


        ############# WE PROBABLY DON'T NEED THESE VARIABLES ANY MORE
        pair_style_name = 'lj/class2/coul/cut'
        pair_style_link = 'http://lammps.sandia.gov/doc/pair_class2.html'
        pair_style_args = '10.5'
        pair_style_command = "    pair_style hybrid " + \
            pair_style_name + " " + pair_style_args + "\n"
        bond_style_name = 'class2'
        bond_style_link = bond_style2docs[bond_style_name]
        bond_style_args = ''
        angle_style_name = 'class2'
        angle_style_link = angle_style2docs[angle_style_name]
        angle_style_args = ''
        dihedral_style_name = 'class2'
        dihedral_style_link = dihedral_style2docs[dihedral_style_name]
        dihedral_style_args = ''
        improper_style_name = 'class2'
        improper_style_link = improper_style2docs[improper_style_name]
        improper_style_args = ''
        hbond_style_name = ''
        hbond_style_link = ''
        hbond_style_args = ''

    
        argv = [arg for arg in sys.argv]
    
        i = 1
    
        while i < len(argv):
    
            #sys.stderr.write('argv['+str(i)+'] = \"'+argv[i]+'\"\n')
    
            if argv[i] == '-atoms':
                if i + 1 >= len(argv):
                    raise Exception('Error: the \"' + argv[i] + '\" argument should be followed by a quoted string\n'
                                    '       which contains a space-delimited list of of a subset of atom types\n'
                                    '       you want to use from the original force-field.\n'
                                    '       Make sure you enclose the entire list in quotes.\n')
                type_subset = Set(argv[i + 1].strip('\"\'').strip().split())
                del argv[i:i + 2]
    
            elif argv[i] == '-name':
                if i + 1 >= len(argv):
                    raise Exception(
                        'Error: ' + argv[i] + ' flag should be followed by the name of the force-field\n')
                ffname = argv[i + 1]
                del argv[i:i + 2]
    
            elif argv[i] in ('-file', '-in-file'):
                if i + 1 >= len(argv):
                    raise Exception(
                        'Error: ' + argv[i] + ' flag should be followed by the name of a force-field file\n')
                filename_in = argv[i + 1]
                try:
                    file_in = open(filename_in, 'r')
                except IOError:
                    sys.stderr.write('Error: Unable to open file\n'
                                     '       \"' + filename_in + '\"\n'
                                     '       for reading.\n')
                    sys.exit(1)
                del argv[i:i + 2]
    
            elif argv[i] == '-pair-cutoff':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by a number'
                                    '       (or two numbers enclosed in a single pair of quotes)\n')
                pair_style_args = argv[i+1]
                del argv[i:i + 2]

            elif argv[i] == '-pair-style':
                if i + 1 >= len(argv):
                    raise Exception(
                        'Error: ' + argv[i] + ' flag should be followed by either \"lj/class2/coul/cut\" or \"lj/class2/coul/long\"\n')
                pair_style_name = argv[i + 1]
                pair_style_args = ''
                if pair_style_name.find('lj/class2/coul/cut') == 0:
                    n = len('lj/class2/coul/cut')
                    pair_style_args = pair_style_name[n+1:]
                    pair_style_name = pair_style_name[:n]
                    pair_style_link = "http://lammps.sandia.gov/doc/pair_class2.html"
                    kspace_style = ''
                elif pair_style_name.find('lj/class2/coul/long') == 0:
                    n = len('lj/class2/coul/long')
                    pair_style_args = pair_style_name[n+1:]
                    pair_style_name = pair_style_name[:n]
                    pair_style_link = "http://lammps.sandia.gov/doc/pair_class2.html"
                    kspace_style = 'kspace_style pppm 0.0001'
                elif pair_style_name.find('lj/cut') == 0:
                    n = len('lj/cut')
                    pair_style_args = pair_style_name[n+1:]
                    pair_style_name = pair_style_name[:n]
                    pair_style_link = "http://lammps.sandia.gov/doc/pair_lj.html"
                    kspace_style = ''
                elif pair_style_name.find('lj/cut/coul/long') == 0:
                    n = len('lj/cut/coul/long')
                    pair_style_args = pair_style_name[n+1:]
                    pair_style_name = pair_style_name[:n]
                    pair_style_link = "http://lammps.sandia.gov/doc/pair_lj.html"
                    kspace_style = 'kspace_style pppm 0.0001'
                else:
                    raise Exception('Error: ' + argv[i] + ' ' + pair_style_name + ' not supported.\n'
                                    '          The following pair_styles are supported:\n'
                                    '       lj/class2/coul/cut\n'
                                    '       lj/class2/coul/long\n'
                                    '       lj/cut\n'
                                    '       lj/cut/coul/long\n')
                del argv[i:i + 2]

            elif argv[i] == '-bond-style':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by\n'
                                    '       a compatible bond_style.\n')
                bond_style_name = argv[i + 1]
                if bond_style_name.find('harmonic') == 0:
                    n = len('harmonic')
                    bond_style_args = bond_style_name[n+1:]
                    bond_style_name = bond_style_name[:n]
                    bond_style_link = 'http://lammps.sandia.gov/doc/bond_harmonic.html'
                elif bond_style_name.find('morse') == 0:
                    n = len('morse')
                    bond_style_args = bond_style_name[n+1:]
                    bond_style_name = bond_style_name[:n]
                    bond_style_link = 'http://lammps.sandia.gov/doc/bond_morse.html'
                elif bond_style_name.find('class2') == 0:
                    n = len('class2')
                    bond_style_args = bond_style_name[n+1:]
                    bond_style_name = bond_style_name[:n]
                    bond_style_link = 'http://lammps.sandia.gov/doc/bond_class2.html'
                else:
                    raise Exception('Error: ' + argv[i] + ' must be followed by either \"harmonic\", \"class2\", or \"morse\".\n')
                del argv[i:i + 2]

            elif argv[i] == '-angle-style':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by\n'
                                    '       a compatible angle_style.\n')
                angle_style_name = argv[i + 1]
                if angle_style_name.find('harmonic') == 0:
                    n = len('harmonic')
                    angle_style_args = angle_style_name[n+1:]
                    angle_style_name = angle_style_name[:n]
                    angle_style_link = 'http://lammps.sandia.gov/doc/angle_harmonic.html'
                elif angle_style_name.find('quartic') == 0:
                    n = len('quartic')
                    angle_style_args = angle_style_name[n+1:]
                    angle_style_name = angle_style_name[:n]
                    angle_style_link = 'http://lammps.sandia.gov/doc/angle_quartic.html'
                elif angle_style_name.find('class2') == 0:
                    n = len('class2')
                    angle_style_args = angle_style_name[n+1:]
                    angle_style_name = angle_style_name[:n]
                    angle_style_link = 'http://lammps.sandia.gov/doc/angle_class2.html'
                else:
                    raise Exception('Error: ' + argv[i] + ' must be followed by either \"harmonic\" or \"class2\"\n')
                del argv[i:i + 2]

            elif argv[i] == '-dihedral-style':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by\n'
                                    '       a compatible dihedral_style.\n')
                dihedral_style_name = argv[i + 1]
                if dihedral_style_name.find('charmm') == 0:
                    n = len('harmonic')
                    dihedral_style_args = dihedral_style_name[n+1:]
                    dihedral_style_name = dihedral_style_name[:n]
                    dihedral_style_link = 'http://lammps.sandia.gov/doc/dihedral_charmm.html'
                elif dihedral_style_name.find('class2') == 0:
                    n = len('class2')
                    dihedral_style_args = dihedral_style_name[n+1:]
                    dihedral_style_name = dihedral_style_name[:n]
                    dihedral_style_link = 'http://lammps.sandia.gov/doc/dihedral_class2.html'
                else:
                    raise Exception('Error: ' + argv[i] + ' must be followed by either \"harmonic\" or \"class2\"\n')
                del argv[i:i + 2]

            elif argv[i] == '-impropoer-style':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by\n'
                                    '       a compatible impropoer_style.\n')
                impropoer_style_name = argv[i + 1]
                if impropoer_style_name.find('harmonic') == 0:
                    n = len('harmonic')
                    impropoer_style_args = impropoer_style_name[n+1:]
                    impropoer_style_name = impropoer_style_name[:n]
                    impropoer_style_link = 'http://lammps.sandia.gov/doc/impropoer_harmonic.html'
                elif impropoer_style_name.find('class2') == 0:
                    n = len('class2')
                    impropoer_style_args = impropoer_style_name[n+1:]
                    impropoer_style_name = impropoer_style_name[:n]
                    impropoer_style_link = 'http://lammps.sandia.gov/doc/impropoer_class2.html'
                else:
                    raise Exception('Error: ' + argv[i] + ' must be followed by either \"harmonic\" or \"class2\"\n')
                del argv[i:i + 2]

            elif argv[i] == '-hbond-style':
                if i + 1 >= len(argv):
                    raise Exception('Error: ' + argv[i] + ' ' + hbond_style_name + '\n'
                                    '       should be followed by a compatible pair_style.\n')
                hbond_style_name = argv[i + 1]
                hbond_style_link = 'http://lammps.sandia.gov/doc/pair_hbond_dreiding.html'
                if hbond_style_name.find('none') == 0:
                    hbond_style_name = ''
                    hbond_style_args = ''
                elif hbond_style_name.find('hbond/dreiding/lj') == 0:
                    n = len('hbond/dreiding/lj')
                    hbond_style_args = hbond_style_name[n+1:]
                    hbond_style_name = hbond_style_name[:n]
                elif hbond_style_name.find('hbond/dreiding/morse') == 0:
                    n = len('hbond/dreiding/morse')
                    hbond_style_args = hbond_style_name[n+1:]
                    hbond_style_name = hbond_style_name[:n]
                else:
                    raise Exception('Error: ' + argv[i] + ' flag should be followed by either\n'
                                    '       \"hbond/dreiding/lj\" or \"hbond/dreiding/morse"\n')
                del argv[i:i + 2]

            elif argv[i] in ('-url', '-in-url'):
                import urllib2
                if i + 1 >= len(argv):
                    raise Exception(
                        'Error: ' + argv[i] + ' flag should be followed by the name of a force-field file.\n')
                url = argv[i + 1]
                try:
                    request = urllib2.Request(url)
                    file_in = urllib2.urlopen(request)
                except urllib2.URLError:
                    sys.stdout.write("Error: Unable to open link:\n" + url + "\n")
                    sys.exit(1)
                del argv[i:i + 2]
    
            elif argv[i] == '-auto':
                include_auto_equivalences = True
                del argv[i:i + 1]
    
            elif argv[i] in ('-help', '--help', '-?', '--?'):
                sys.stderr.write(doc_msg)
                sys.exit(0)
                del argv[i:i + 1]
    
            else:
                i += 1
    
        if len(argv) != 1:
            raise Exception('Error: Unrecongized arguments: ' + ' '.join(argv[1:]) +
                            '\n\n' + doc_msg)
    
        #sys.stderr.write("Reading parameter file...\n")
    
        lines = file_in.readlines()
        atom2charge = OrderedDict()  # lookup charge from atom type
        atom2mass = OrderedDict()  # lookup mass from atom type
        atom2ffid = OrderedDict()  # lookup "force-field-ID" a string containing
                                   # equivalences to lookup bonded interactions
        atompair2pair = OrderedDict() # lookup a tuple with pair_type and the
                                      # parameter list for each atom type pair.
        atom2element = OrderedDict()  # Optional:
                                      # which element (eg 'C', 'O') ? (Note this
                                      # is different from atom type: 'C1', 'Oh')
        atom2num_bonds = OrderedDict() # Optional: how many bonds emanate from
        atom2descr = OrderedDict()    # Optional: a brief description
        atom2bond = OrderedDict()
        lines_equivalences = []      # equivalences for force-field lookup
        lines_auto_equivalences = [] # auto_equivalences have lower priority

        bonds_increments = OrderedDict()  # lookup partial charge contributions

        bond2param = OrderedDict()  # store a tuple with the 2-body bond
                                    # interaction type, and its parameters
                                    # for every type of bond
        bond2priority = OrderedDict()  # What is the priority of this interaction?
        bond2style = OrderedDict()    # What LAMMPS bond style (formula)
                                      # is used for a given interaction?
        bond_styles = set([])         # Contains all bond styles used.

        angle2param = OrderedDict() # store a tuple with the 3-body angle
                                    # interaction type, and its parameters
                                    # for every type of angle

        # http://lammps.sandia.gov/doc/angle_class2.html
        #angle2class2_a = OrderedDict()  # params for the "a" class2 terms
        angle2class2_bb = OrderedDict() # params for the "bb" class2 terms
        angle2class2_ba = OrderedDict() # params for the "ba" class2 terms
        angle2priority = OrderedDict()  # What is the priority of this interaction?
        angle2style = OrderedDict()    # What LAMMPS angle style (formula)
                                       # is used for a given interaction?
        angle_styles = set([])         # Contains all angle styles used.

        # http://lammps.sandia.gov/doc/dihedral_class2.html
        dihedral2param = OrderedDict() # store a tuple with the 4-body dihedral
                                       # interaction type, and its parameters
                                       # for every type of dihedral
        #dihedral2class2_d = OrderedDict() # params for the "d" class2 term
        dihedral2class2_mbt = OrderedDict() # params for the "mbt" class2 term
        dihedral2class2_ebt = OrderedDict() # params for the "ebt" class2 term
        dihedral2sym_ebt = OrderedDict()
        dihedral2class2_at = OrderedDict() # params for the "at" class2 term
        dihedral2class2_at = OrderedDict()
        dihedral2class2_aat = OrderedDict() # params for the "aat" class2 term
        dihedral2sym_aat = OrderedDict()
        dihedral2class2_bb13 = OrderedDict() # params for the "bb13" class2 term
        dihedral2sym_bb13 = OrderedDict()
        dihedral2priority = OrderedDict()  # What is the priority of this interaction?
        dihedral2style = OrderedDict()    # What LAMMPS dihedral style (formula)
                                          # is used for a given interaction?
        dihedral_styles = set([])         # Contains all dihedral styles used.


        # http://lammps.sandia.gov/doc/improper_class2.html
        improper2param = OrderedDict() # store a tuple with the 4-body improper
                                       # interaction type, and its parameters
                                       # for every type of imporpoer
        #improper2class2_i = OrderedDict() # params for the "i" class2 term
        improper2class2_aa = OrderedDict() # params for the "aa" class2 term

        improper2cross = DefaultDict(dict)
                           # improper2cross[imp_name][atoms] stores the 
                           # coefficient (K) for the angle-angle ("aa") 
                           # improper interactions between a pair of 
                           # neighboring 3-body angles (in the .FRC file).
                           # "imp_name" is the name of the improper interaction
                           #   (which is a concatination of the central atom and
                           #   the 3 surrounding leaf atoms (which are sorted))
                           # "atoms" indicates, for that K value, the list of
                           #   leaf atoms for that K value as they appear in the
                           #   corresponding line of the .frc file (however the
                           #   and last atom names are swapped if the first
                           #   atom name is lexicographically > the last, to
                           #   eliminate redundancy and ambiguity.)

        improper2sym = DefaultDict(set)
                           # improper2sym[imp_name] indicates which subset of
                           # leaf atoms (from 0 to 2) are equivalent and can
                           # tolerate having their order rearranged without
                           # effecting the energy.  Later on this will be used
                           # to reduce the number of improper interactions that
                           # will be generated by moltemplate.

        improper2priority = OrderedDict() # What is the priority of this interaction?
        improper2style = OrderedDict()    # What LAMMPS improper style (formula)
                                          # is used for a given interaction?
        improper_styles = set([])         # Contains all improper styles used.


        # Warn users if force field contains terms which cannot yet
        # be simulated with LAMMPS (as of 2017-2-07)
        display_OOP_OOP_warning = False
        display_torsion_torsion_1_warning = False


        """
         --- these next few lines of code appear to be unnecessary.
         --- I'll probably delete this code in a later version
        hbond2params = OrderedDict()    # lookup hbond parameters and atom types
        hbond2donors = OrderedDict()    # according to the identifier in the 2nd
        hbond2acceptors = OrderedDict() #  column of the "#hbond_definition"
        hbond2hydrogens = OrderedDict() # section of an .frc file.
        """

        allowed_section_names = set(['#define',
                                     # sections used in all MSI force-fields
                                     '#atom_types',
                                     '#equivalence',
                                     '#auto_equivalence', # cvff_auto
                                     '#nonbond(9-6)',
                                     '#nonbond(12-6)'
                                     '#quadratic_bond',
                                     '#quartic_bond',
                                     '#morse_bond',       # cvff
                                     '#quadratic_angle',
                                     '#quartic_angle',
                                     '#bond-bond',
                                     '#bond-angle',
                                     '#torsion_1',
                                     '#torsion_3',
                                     '#middle_bond-torsion_3',
                                     '#end_bond-torsion_3',
                                     '#angle-torsion_3',
                                     '#angle-angle-torsion_1',#(class2 dihedral)
                                     '#bond-bond_1_3', #(a class2 dihedral term)
                                     '#out_of_plane',
                                     '#wilson_out_of_plane',
                                     '#angle-angle',   #(a class2 improper term)
                                     '#out_of_plane-out_of_plane', # UNSUPPORTED
                                     '#torsion-torsion_1',         # UNSUPPORTED
                                     '#bond_increments',
                                     '#hbond_definition'           # irrelevant
                                     ])

        icol_type = icol_mass = icol_elem = icol_nbond = icol_comment = -1

        section_name = ''
        section_is_auto = False

        for iline in range(0, len(lines)):
            line = lines[iline]
            tokens = SplitQuotedString(line.strip(),
                                       comment_char='!>'))
            if (len(tokens) > 0) and (tokens[0] in allowed_section_names):
                section_name = tokens[0]
                section_is_auto = (tokens[-1][-5] == '_auto')
            elif (len(tokens) == 8) and (section_name == '#equivalence'):
                lines_equivalences.append(line)
            elif (len(tokens) == 12) and (section_name == '#auto_equivalence'):
                lines_auto_equivalences.append(line)
            elif (len(tokens) > 1) and (section_name == '#atom_types'):
                # Different FRC files put this information in different
                # columns.  Column order is stored in the !Ver comment line:
                if line.strip().find('!Ver') == 0:
                    tokens = line.strip().split()
                    for i in range(0, len(tokens)):
                        if tokens[i].lower() == 'type':
                            icol_type = i
                        elif tokens[i].lower() == 'mass':
                            icol_mass = i
                        elif tokens[i].lower() == 'element':
                            icol_elem = i
                        elif tokens[i].lower() == 'connections':
                            icol_nbond = i
                        elif tokens[i].lower() == 'comment':
                            icol_comment = i

                # "else" is not needed here because '!Ver' is a comment, 
                # and any line beginning with '!Ver' will be ignored 
                # when we set comment_char='!' below:

                tokens = map(RemoveOuterQuotes,
                             NSplitQuotedString(line.strip(),
                                                icol_comment,
                                                comment_char='!'))
                if (len(tokens) > 5):
                    if -1 in (icol_type, icol_mass):
                        raise Exception('Error: Invalid #atom_types section.\n'
                                        '       The meaning of each column cannot be determined.\n'
                                        '       This file needs a valid "!Ver..." comment.\n')
                    if icol_comment = -1:
                        icol_comment = max(icol_type, icol_mass,
                                           icol_elem, icol_nbond) + 1

                    if ((len(type_subset) == 0) or (tokens[1] in type_subset)):
                        atom2mass[tokens[icol_type]] = max(float(tokens[icol_mass]), 1.0)
                        atom2element[tokens[icol_type]] = tokens[icol_elem]
                        atom2numbonds[tokens[icol_type]] = int(tokens[icol_nbond])
                        #atom2mass[tokens[2]] = float(tokens[6])
                        # Some atoms in cvff.prm have zero mass. Unfortunately this
                        # causes LAMMPS to crash, even if these atoms are never used,
                        # so I give the mass a non-zero value instead.
                        atom2descr[tokens[1]] = line[51:]
                else:
                    raise Exception('Error: Invalid atom line:\n' + line)

            elif (len(tokens) > 4) and (section_name == '#nonbond(12-6)'):
                atom_name = tokens[2]
                A = float(tokens[3])
                B = float(tokens[4])
                epsilon = B*B/(4*A)
                sigma = pow(B/A, 1.0/6)
                if sigma == 0.0:
                    sigma = 1.0   #(non-zero to avoid nan error later)
                pair2style[atom_name] = 'lj/cut/long'
                pair2params[atom_name] = (str(epsilon)+' '+str(sigma))
                pair_mixing_style = 'geometric tail yes'
                #if pair_style_name.find('lj/cut') == 0:
                #    pair2params[atom_name] = (str(epsilon)+' '+str(sigma))
                #    pair_mixing_style = 'geometric tail yes'

            elif (len(tokens) > 4) and (section_name == '#nonbond(9-6)'):
                atom_name = tokens[2]
                sigma = tokens[3]
                epsilon = tokens[4]
                pair2style[atom_name] = 'class2'
                pair2params[atom_name] = (epsilon+' '+sigma)
                pair_mixing_style = 'sixthpower tail yes'
                #if pair_style_name.find('lj/class2') == 0:
                #    pair2params[atom_name] = (epsilon+' '+sigma)
                #    pair_mixing_style = 'sixthpower tail yes'

            elif (len(tokens) > 6) and (section_name == '#bond_increments'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:4]))
                bond_name = EncodeInteractionName(atom_names, section_is_auto)
                deltaIJ = tokens[4]
                deltaJI = tokens[5]  # (always -deltaIJ?)
                bond_increments[bond_name] = (deltaIJ+' '+deltaJI)

            elif (len(tokens) > 6) and (section_name == '#quadratic-bond'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:4]))
                bond_name = EncodeInteractionName(atom_names, section_is_auto)
                bond2priority[bond_name] = (section_is_auto, #auto->lowest priority
                                            DeterminePriority(tokens[2:4]))
                r0 = tokens[4]
                bond2r0[bond_name] = r0
                k = tokens[5]
                r0 = tokens[6]
                bond2style[bond_name] = 'harmonic'
                bond2params[bond_name] = (k+' '+r0)
                #if bond_style_name == 'harmonic':
                #    bond2params[bond_name] = (k+' '+r0)

            elif (len(tokens) > 7) and (section_name == '#quartic-bond'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:4]))
                bond_name = EncodeInteractionName(atom_names, section_is_auto)
                bond2priority[bond_name] = (section_is_auto, #auto->lowest priority
                                            DeterminePriority(tokens[2:4]))
                r0 = tokens[4]
                bond2r0[bond_name] = r0
                K2 = tokens[5]
                K3 = tokens[6]
                K4 = tokens[7]
                bond2style[bond_name] = 'quartic'
                bond2params[bond_name] = (r0+' '+K2+' '+K3+' '+K4)
                #if bond_style_name in ('quartic', 'class2'):
                #    bond2params[bond_name] = (r0+' '+K2+' '+K3+' '+K4)

            elif (len(tokens) > 7) and (section_name == '#quadratic-angle'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:5]))
                angle_name = EncodeInteractionName(atom_names, section_is_auto)
                angle2priority[angle_name] = (section_is_auto, #auto-->low priority
                                              DeterminePriority(tokens[2:5]))
                theta0 = tokens[5]
                angle2theta0[angle_name] = theta0
                k = tokens[6]
                theta0 = tokens[7]
                angle2style[angle_name] = 'harmonic'
                angle2params[angle_name] = (k+' '+theta0)
                #if angle_style_name == 'harmonic':
                #    angle2params[angle_name] = (k+' '+theta0)

            elif (len(tokens) > 8) and (section_name == '#quartic-angle'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:5]))
                angle_name = EncodeInteractionName(atom_names, section_is_auto)
                angle2priority[angle_name] = (section_is_auto, #auto-->low priority
                                              DeterminePriority(tokens[2:5]))
                theta0 = tokens[5]
                angle2theta0[angle_name] = theta0
                K2 = tokens[6]
                K3 = tokens[7]
                K4 = tokens[8]
                angle2style[angle_name] = 'class2'
                #angle2class2_a[angle_name] = (theta0+' '+K2+' '+K3+' '+K4)
                angle2params[angle_name] = (theta0+' '+K2+' '+K3+' '+K4)

            elif (len(tokens) > 5) and (section_name == '#bond-bond'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:5]))
                angle_name = EncodeInteractionName(atom_names, section_is_auto)
                angle2priority[angle_name] = (section_is_auto, #auto-->low priority
                                              DeterminePriority(tokens[2:5]))
                Kbb = tokens[5]
                bond_names = [EncodeInteractionName(ReverseIfEnds(aorig[0:2]),
                                                    section_is_auto),
                              EncodeInteractionName(ReverseIfEnds(aorig[2:4]),
                                                    section_is_auto)]
                r0 = [bond2r0[bond_names[0]],
                      bond2r0[bond_names[1]]]
                if order_reversed:
                    r0.reverse()
                angle2style[angle_name] = 'class2'
                angle2class2_bb[angle_name] = (Kbb+' '+r0[0]+' '+r0[1])
                #if angle_style_name == 'class2':
                #    angle2params_bb[angle_name] = (Kbb+' '+r0a+' '+r0b)

            elif (len(tokens) > 5) and (section_name == '#bond-angle'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:5]))
                angle_name = EncodeInteractionName(atom_names, section_is_auto)
                angle2priority[angle_name] = (section_is_auto, #auto-->low priority
                                              DeterminePriority(tokens[2:5]))
                K=['','']
                K[0] = tokens[5]
                K[1] = K[0]
                if len(tokens) > 5:
                    K[1] = tokens[6]
                bond_names = [EncodeInteractionName(ReverseIfEnds(aorig[0:2]),
                                                    section_is_auto),
                              EncodeInteractionName(ReverseIfEnds(aorig[2:4]),
                                                    section_is_auto)]
                r0 = [bond2r0[bond_names[0]],
                      bond2r0[bond_names[1]]]
                if order_reversed:
                    K.reverse()
                    r0.reverse()
                angle2style[angle_name] = 'class2'
                angle2params_ba[angle_name]= (K[0]+' '+K[0]+' '+r0[0]+' '+r0[1])
                #if angle_style_name == 'class2':
                #    angle2params_ba[angle_name]= (Ka+' '+Kb+' '+r0a+' '+r0b)

            elif (len(tokens) > 8) and (section_name == '#torsion_1'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:6]))
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                K = tokens[6]
                n = tokens[7]
                d = tokens[8]
                w = '0.0'  #ignore: this is only used by the CHARMM force field
                dihedral2style[dihedral_name] = 'charmm'
                dihedral2params[dihedral_name] = (K+' '+n+' '+d+' '+w)
                #if dihedral_style_name == 'charmm':
                #    dihedral2params[dihedral_name] = (K+' '+n+' '+d+' '+w)

            elif (len(tokens) > 7) and (section_name == '#torsion_3'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:6]))
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                V1 = tokens[6]
                phi0_1 = tokens[7]
                V2 = phi0_2 = V3 = phi0_3 = '0.0'
                if len(tokens) > 9:
                    V2 = tokens[8]
                    phi0_2 = tokens[9]
                if len(tokens) > 11:
                    V3 = tokens[10]
                    phi0_3 = tokens[11]
                dihedral2style[dihedral_name] = 'class2'
                dihedral2params[dihedral_name] = (V1+' '+phi0_1+' '+
                                                  V2+' '+phi0_2+' '+
                                                  V3+' '+phi0_3)
                #if dihedral_style_name == 'class2':
                #    dihedral2params[dihedral_name] = (V1+' '+phi0_1+' '+
                #                                      V2+' '+phi0_2+' '+
                #                                      V3+' '+phi0_3)

            elif (len(tokens) > 6) and (section_name == '#middle_bond-torsion_3'):
                atom_names = ReverseIfEnds(map(EncodeAName, tokens[2:6]))
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                F1 = tokens[6]
                F2 = F3 = '0.0'
                if len(tokens) > 7:
                    F2 = tokens[7]
                if len(tokens) > 8:
                    F3 = tokens[8]
                bond_name = EncodeInteractionName(atom_names[1:3],
                                                  section_is_auto)
                r0 = bond2r0[bond_name]
                dihedral2style[dihedral_name] = 'class2'
                dihedral2params_mbt[dihedral_name]= (F1+' '+F2+' '+
                                                     F3+' '+r0)
                #if dihedral_style_name == 'class2':
                #    dihedral2params_mbt[dihedral_name]= (F1+' '+F2+' '+
                #                                         F3+' '+r0)


            elif (len(tokens) > 6) and (section_name == '#end_bond-torsion_3'):
                aorig = map(EncodeAName, tokens[2:6])
                atom_names = ReverseIfEnd(aorig)
                order_reversed = aorig[0] > aorig[-1]
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                F = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
                F[0][0] = tokens[6]
                if len(tokens) > 7:
                    F[0][1] = tokens[7]
                if len(tokens) > 8:
                    F[0][2] = tokens[8]
                F[1][0] = F[0][0]
                F[1][1] = F[0][1]
                F[1][2] = F[0][2]
                if len(tokens) > 9:
                    F[1][0] = tokens[9]
                if len(tokens) > 10:
                    F[1][1] = tokens[10]
                if len(tokens) > 11:
                    F[1][2] = tokens[11]
                bond_names = [EncodeInteractionName(ReverseIfEnds(aorig[0:2]),
                                                    section_is_auto),
                              EncodeInteractionName(ReverseIfEnds(aorig[2:4]),
                                                    section_is_auto)]
                r0 = [bond2r0[bond_names[0]],
                      bond2r0[bond_names[1]]]
                if order_reversed:
                    F.reverse()
                    r0.reverse()
                dihedral2style[dihedral_name] = 'class2'
                dihedral2class2_ebt[dihedral_name]= (F[0][0] + ' ' +
                                                     F[0][1] + ' ' +
                                                     F[0][2] + ' ' +
                                                     F[1][0] + ' ' +
                                                     F[1][1] + ' ' +
                                                     F[1][2] + ' ' +
                                                     r0[0]+' '+r0[1])
                dihedral2sym_ebt[dihedral_name] = ((F[0][0] == F[1][0]) and
                                                   (F[0][1] == F[1][1]) and
                                                   (F[0][2] == F[1][2]) and
                                                   (r0[0] == r0[1]))

                #if dihedral_style_name == 'class2':
                #    dihedral2class2_ebt[dihedral_name]= (F[0][0] + ' ' +
                #                                         F[0][1] + ' ' +
                #                                         F[0][2] + ' ' +
                #                                         F[1][0] + ' ' +
                #                                         F[1][1] + ' ' +
                #                                         F[1][2] + ' ' +
                #                                         r0[0]+' '+r0[1])
                #    dihedral2sym_ebt[dihedral_name] = ((F[0][0] == F[1][0]) and
                #                                       (F[0][1] == F[1][1]) and
                #                                       (F[0][2] == F[1][2]) and
                #                                       (r0[0] == r0[1]))
               

            elif (len(tokens) > 6) and (section_name == '#angle-torsion_3'):
                aorig = map(EncodeAName, tokens[2:6])
                atom_names = ReverseIfEnd(aorig)
                order_reversed = aorig[0] > aorig[-1]
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                F = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
                F[0][0] = tokens[6]
                if len(tokens) > 7:
                    F[0][1] = tokens[7]
                if len(tokens) > 8:
                    F[0][2] = tokens[8]
                F[1][0] = F[0][0]
                F[1][1] = F[0][1]
                F[1][2] = F[0][2]
                if len(tokens) > 9:
                    F[1][0] = tokens[9]
                if len(tokens) > 10:
                    F[1][1] = tokens[10]
                if len(tokens) > 11:
                    F[1][2] = tokens[11]
                angle_names = [EncodeInteractionName(ReverseIfEnds(aorig[0:3]),
                                                     section_is_auto),
                               EncodeInteractionName(ReverseIfEnds(aorig[0:3]),
                                                     section_is_auto)]
                theta0 = [angle2theta0[angle_names[0]],
                          angle2theta0[angle_names[1]]]
                if order_reversed:
                    F.reverse()
                    theta0.reverse()
                dihedral2style[dihedral_name] = 'class2'
                dihedral2class2_at[dihedral_name]= (F[0][0] + ' ' +
                                                    F[0][1] + ' ' +
                                                    F[0][2] + ' ' +
                                                    F[1][0] + ' ' +
                                                    F[1][1] + ' ' +
                                                    F[1][2] + ' ' +
                                                    theta0[0] + ' '+
                                                    theta0[1])
                dihedral2sym_at[dihedral_name] = ((F[0][0] == F[1][0]) and
                                                  (F[0][1] == F[1][1]) and
                                                  (F[0][2] == F[1][2]) and
                                                  (theta0[0] == theta0[1]))

                #if dihedral_style_name == 'class2':
                #    dihedral2class2_at[dihedral_name]= (F[0][0] + ' ' +
                #                                        F[0][1] + ' ' +
                #                                        F[0][2] + ' ' +
                #                                        F[1][0] + ' ' +
                #                                        F[1][1] + ' ' +
                #                                        F[1][2] + ' ' +
                #                                        theta0[0] + ' '+
                #                                        theta0[1])
                #    dihedral2sym_at[dihedral_name] = ((F[0][0] == F[1][0]) and
                #                                      (F[0][1] == F[1][1]) and
                #                                      (F[0][2] == F[1][2]) and
                #                                      (theta0[0] == theta0[1]))
                    


            elif (len(tokens) > 6) and (section_name == '#angle-angle-torsion_1'):
                aorig = map(EncodeAName, tokens[2:6]))
                atom_names = ReverseIfEnd(aorig)
                order_reversed = aorig[0] > aorig[-1]
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                Kaa = tokens[6]
                angle_names = [EncodeInteractionName(ReverseIfEnds(aorig[0:3]),
                                                     section_is_auto),
                               EncodeInteractionName(ReverseIfEnds(aorig[0:3]),
                                                     section_is_auto)]
                theta0 = [angle2theta0[angle_names[0]],
                          angle2theta0[angle_names[1]]]
                order_reversed == (aorig[0] > aorig[-1])
                if order_reversed:
                    angle_names.reverse()
                    theta0.reverse()
                dihedral2style[dihedral_name] = 'class2'
                dihedral2class2_aat[dihedral_name]= (Kaa+' '+
                                                     theta0[0]+' '+
                                                     theta0[1])
                dihedral2sym_aat[dihedral_name] = (theta0[0] == theta0[1])
                #if dihedral_style_name == 'class2':
                #    dihedral2class2_aat[dihedral_name]= (Kaa+' '+
                #                                         theta0[0]+' '+
                #                                         theta0[1])
                #    dihedral2sym_aat[dihedral_name] = (theta0[0] == theta0[1])
                    

            elif (len(tokens) > 6) and (section_name == '#bond-bond_1_3'):
                aorig = map(EncodeAName, tokens[2:6]))
                atom_names = ReverseIfEnd(aorig)
                order_reversed = aorig[0] > aorig[-1]
                dihedral_name = EncodeInteractionName(atom_names, section_is_auto)
                dihedral2priority[dihedral_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                Kbb = tokens[6]
                bond_names = [EncodeInteractionName(atom_names[0:2],
                                                    section_is_auto),
                              EncodeInteractionName(atom_names[2:4],
                                                    section_is_auto)]
                r0 = [bond2r0[bond_names[0]]
                      bond2r0[bond_names[1]]]
                order_reversed == (aorig[0] > aorig[-1])
                if order_reversed:
                    bond_names.reverse()
                    r0.reverse()
                dihedral2style[dihedral_name] = 'class2'
                dihedral2class2_bb13[dihedral_name] = (Kbb + ' ' +
                                                       r0[0] + ' ' +
                                                       r0[1])
                dihedral2sym_bb13[dihedral_name] = (r0[0] == r0[1])
                #if dihedral_style_name == 'class2':
                #    dihedral2class2_bb13[dihedral_name] = (Kbb + ' ' +
                #                                           r0[0] + ' ' +
                #                                           r0[1])
                #    dihedral2sym_bb13[dihedral_name] = (r0[0] == r0[1])


            elif (len(tokens) > 8) and (section_name == '#out_of_plane'):
                atom_names,  = OOPImproperNameSort(tokens[2:6])
                improper_name = EncodeInteractionName(atom_names, section_is_auto)
                improper2priority[improper_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                K = tokens[6]
                n = tokens[7]
                chi0 = tokens[8]
                improper2style[improper_name] = 'cvff'
                improper2params[improper_name] = (Kchi+' '+n+' '+chi0)
                improper_symmetry_subgraph = 'cenJswapIL'
                #if improper_style_name == 'cvff':
                #    improper2params[improper_name] = (Kchi+' '+n+' '+chi0)
                #    improper_symmetry_subgraph = 'cenJswapIL'

            elif (len(tokens) > 7) and (section_name == '#wilson_out_of_plane'):
                atom_names, = Class2ImproperNameSort(tokens[2:6])
                improper_name = EncodeInteractionName(atom_names, section_is_auto)
                improper2priority[improper_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                K = tokens[6]
                chi0 = tokens[7]
                improper2style[improper_name] = 'class2'
                #improper2class2_i[improper_name] = (K+' '+chi0)
                improper2params[improper_name] = (K+' '+chi0)
                improper_symmetry_subgraph = 'cenJsortIKL'
                #if improper_style_name == 'class2':
                #    improper2class2_i[improper_name] = (K+' '+chi0)
                #    improper_symmetry_subgraph = 'cenJsortIKL'

            elif (len(tokens) > 6) and (section_name == '#angle-angle'):
                improper_i2cross[i] as an index
                atom_names, = Class2ImproperNameSort(tokens[2:6])
                improper_name = EncodeInteractionName(atom_names, section_is_auto)
                improper2priority[improper_name] = (section_is_auto,
                                                    DeterminePriority(tokens[2:6]))
                K = tokens[6]
                improper2cross[improper_name][ImCrossTermID(atom_names)] = K
                improper2style[improper_name] = 'class2'

            elif (len(tokens) > 0) and (section_name == '#out_of_plane-out_of_plane'):
                display_OOP_OOP_warning = True

            elif (len(tokens) > 0) and (section_name == '#torsion-torsion_1'):
                display_torsion_torsion_1_warning = True


            """
             --- these next few lines of code appear to be unnecessary.
             --- I'll probably delete this code in a later version
            elif (len(tokens) > 3) and (section_name == '#hbond_definition'):
                hbondID = tokens[1]
                if tokens[2] == 'distance':
                    hbond2distance[hbondID] = tokens[3]
                if tokens[2] == 'angle':
                    hbond2angle[hbondID] = tokens[3]
                if tokens[2] == 'donors':
                    hbond2donors[hbondID] = map(EncodeAName, tokens[2:]))
                if tokens[2] == 'acceptors':
                    hbond2acceptors[hbondID] = map(EncodeAname(),tokens[2:]))
            """


        if display_OOP_OOP_warning:
            sys.stderr.write('###########################################################\n'
                             'WARNING\n'
                             '      ALL \"out-of-plane_out-of_plane\" INTERACTIONS ARE IGNORED.\n'
                             '      CHECK THAT THESE TERMS ARE NEGLEGIBLY SMALL.\n'
                             '      \"out-of-plane_out-of_plane\" interactions are not yet supported in LAMMPS\n'
                             '      (...as of 2017-2-07)  There is no way that moltemplate can produce\n'
                             '      LAMMPS compatible parameter files for these interactions.\n'
                             '###########################################################\n')

        if display_torsion_torsion_1_warning:
            sys.stderr.write('###########################################################\n'
                             'WARNING\n'
                             '      ALL \"torsion_torsion_1\" INTERACTIONS ARE IGNORED.\n'
                             '      CHECK THAT THESE TERMS ARE NEGLEGIBLY SMALL.\n'
                             '      \"torsion_torsion_1\" interactions are not yet supported in LAMMPS\n'
                             '      (...as of 2017-2-07)  There is no way that moltemplate can produce\n'
                             '      LAMMPS compatible parameter files for these interactions.\n'
                             '###########################################################\n')




        if include_auto_equivalences:
            atom2ffid = Equivalences2ffids(lines_equivalences,
                                       atom_types)
        else:
            atom2ffid = AutoEquivalences2ffids(lines_equivalences,
                                            lines_auto_equivalences,
                                            atom_types)

        # Collect information from the different terms in a class2 dihedral:
        # http://lammps.sandia.gov/doc/dihedral_class2.html

        for dihedral_name in dihedral2sym_at:
            anames = ExtractANames(dihedral_name)     # names of all 4 atoms
            if not (dihedral2sym_ebt[dihedral_name] and
                    dihedral2sym_at[dihedral_name] and
                    dihedral2sym_aat[dihedral_name] and
                    dihedral2sym_bb13[dihedral_name]):
                if ((anames[0] == anames[3]) and
                    (anames[1] == anames[2])):
                    raise Exception('Error: Unsupported dihedral interaction: \"@dihedral:'+str(dihedral_name)+'\"\n'
                                        '       This interaction has symmetric atom names:\n'
)+')\n'
                                        '       and yet it lacks symmetry in the corresponding force field parameters.\n'
                                        '       (If this is not a mistake in the .frc file, then ask andrew to\n'
                                        '       fix this limitation.)\n')


        # Collect information from the different terms in a class2 improper:
        # http://lammps.sandia.gov/doc/improper_class2.html

        for improper_name in improper2cross:
            # Loop over the neighbors of the central atom in each improper
            # interaction and collect all the Mi and Ti parameters. Collect 
            # them in the order they appear in the formula for the Eaa
            # term as it appears in the documentation for improper_style class2:
            # 
            #    http://lammps.sandia.gov/doc/improper_class2.html
            #
            # Eaa = M1 (Tijk - T0)(Tkjl - T2) +   #common leaf node: k (index 2)
            #       M2 (Tijk - T0)(Tijl - T1) +   #common leaf node: i (index 0)
            #       M3 (Tijl - T1)(Tkjl - T2)     #common leaf node: l (index 3)
            # (I'm trying to match the variable names used in this web page
            #  I wish the author had chosen the M1,M2,M3, T1,T2,T3 order in more
            #  symmetric way, or at least in a way that makes more sense to me.)

            is_auto = IsAutoInteraction(improper_name)   # is this an "auto" interaction?

            anames = ExtractANames(improper_name)        # names of all 4 atoms
            lnames = [anames[0], anames[2], anames[3]]   # names of "leaf" atoms

            #M1     = improper2cross[improper_name][ 2 ]
            #M2     = improper2cross[improper_name][ 0 ]
            #M3     = improper2cross[improper_name][ 3 ]
            M1     = improper2cross[improper_name][ImCrossTermID([anames[0],
                                                                  anames[1],
                                                                  anames[2],
                                                                  anames[3]])]
            M2     = improper2cross[improper_name][ImCrossTermID([anames[2],
                                                                  anames[1],
                                                                  anames[0],
                                                                  anames[3]])]
            M3     = improper2cross[improper_name][ImCrossTermID([anames[0],
                                                                  anames[1],
                                                                  anames[3],
                                                                  anames[2]])]

            angle_name_l = ReverseIfEnds([anames[0], anames[1], anames[2]])
            angle_name = EncodeInteractionName(angle_name_l, is_auto)
            theta01 = angle2theta0[angle_name]

            angle_name_l = ReverseIfEnds([anames[0], anames[1], anames[3]])
            angle_name = EncodeInteractionName(angle_name_l, is_auto)
            theta02 = angle2theta0[angle_name]

            angle_name_l = ReverseIfEnds([anames[2], anames[1], anames[3]])
            angle_name = EncodeInteractionName(angle_name_l, is_auto)
            theta03 = angle2theta0[angle_name]

            improper2class2_aa[improper_name] = (M1 + ' ' + M2 + ' ' + M3 + ' '+
                                                 theta01 + ' '
                                                 theta02 + ' '
                                                 theta03)

            # ###### Symmetry: ######
            # Unfortunately, it's time to wade into the messy issue of symmetry.
            #    We desire a way to detect whether an improper interaction
            # between 4 atoms is invariant with respect to atom reordering
            # of the 3 peripheral "leaf" atoms which surround the central atom.
            # In principle, any rearrangement of atoms would require a separate
            # class2 improper interaction.  However, in some cases, when the
            # parameters for these rearrangements are symmetric, we can detect
            # that and warn moltemplate that it is not necessary to generate new
            # improper interactions for every conceivable permutation of these
            # atoms.  Figuring out when it is safe to do that is a headache.
            #   (...but it's necessary.  Otherwise each junction in the molecule
            #   will generate 3*2*1=6 improper interactions which are usually
            #   redundant.  This will slow down the simulation significantly
            #   and may make it difficult to compare the resulting LAMMPS 
            #   input files with those generated by other tools like msi2lmp.)
            #
            # To make this easier, I store the parameters in arrays which 
            # are arranged in a more symmetric way
            M = [0.0, 0.0, 0.0]
            theta0 = [0.0, 0.0, 0.0]
            # noti3[i] = the sorted tuple of integers from the 
            #            set {0,1,2} which remain after deleting i
            noti3 = ((1,2), (0,2), (0,1))
            i_neigh = [ ([0,2,3][ noti3[i][0] ],   # neighbor leaves of ith leaf
                         [0,2,3][ noti3[i][1] ]) for i in range(0,3)]
            for i in range(0, 3):
                # You will notice the pattern "[0,2,3][i]" appears often in the
                # code below because for class 2 force-fields, the second atom
                # (with index 1) is the central atom ("hub" atom), and the three
                # that surround it ("leaf" atoms) have indices 0,2,3.  I want
                # to skip over the central atoms and loop over the leaf atoms
                imTermID = ImCrossTermID([anames[ i_neigh[i][0] ],
                                          anames[ 1 ],
                                          anames[ [0,2,3][i] ],
                                          anames[ i_neigh[i][1] ]])
                M[i] = float(improper2cross[improper_name][imTermID])
                #i_leaf = [0,2,3][i]
                #M[i] = float(improper2cross[improper_name][ i_leaf ])
                angle_name_l = ReverseIfEnds([anames[i_neigh[i][0]],
                                              anames[i],
                                              anames[i_neigh[i][1]]])
                angle_name = EncodeInteractionName(angle_name_l, is_auto)
                theta0[i] = float(angle2theta0[angle_name])

            for i in range(0, 3):
                if ((M[i_neigh[i][0]] == M[i_neigh[i][1]]) and
                    (theta0[ i_neigh[i][1] ] == theta0[ i_neigh[i][1] ])):
                    # Then it is safe to swap the order of these two atoms in
                    # the list of atoms when looking up force-field parameters
                    improper2sym[improper_name].add(i_neigh[i][0])
                    improper2sym[improper_name].add(i_neigh[i][1])
                    # Later, I can use these to decide whether or not I need to
                    # change the default script with symmetry rules. (I'm hoping
                    # that "cenJsortIKL.py" should work in most cases.)
                else:
                    if anames[i_neigh[i][0]] == anames[i_neigh[i][1]]:
                        raise Exception('Error: Unsupported improper interaction: \"@improper:'+str(improper_name)+'\"\n'
                                        '       This interaction has matching aton aliases:\n'
                                        '       (@atom:'+str(anames[i_neigh[i][0]])+
                                        ', @atom:'+str(anames[i_neigh[i][1]])+')\n'
                                        '       and yet it lacks symmetry in the corresponding force field parameters.\n'
                                        '       (If this is not a mistake in the .frc file, then ask andrew to\n'
                                        '       fix this limitation.)\n')






        """
         --- these next few lines of code appear to be unnecessary.
         --- I'll probably delete them eventually
        if len(hbond2params) > 0:
            sys.stdout.write('\n\n  write_once("In Settings") {\n')
            if hbond_style == 'hbond/dreiding/lj':
                for hbondID, angle in hbond2angle:
                    hbond2params[hbondID] =  hbond2distance[hbondID]+' '+hbond2angle[hbondID]  ##<--this is not correct
            for hbondID, params in hbond2params:
                for donor in hbond2donors[hbondID]:
                    for acceptor in hbond2acceptors[hbondID]:
                        for hydrogen in hbond2hydrogens[hbondID]:
                            sys.stdout.write('pair_coeff @atom:'+donor+' @atom:'+acceptor+' '+hbond_style+' @atom:'+hydrogen+' i '+params+'\n')
            sys.stdout.write('  }   # (DREIDING style H-bond parameters)\n\n\n')
        """

        sys.stderr.write(" done.\n")
        sys.stderr.write("Converting to moltemplate format...\n")








        CONTINUEHERE








        sys.stdout.write("# This file was generated automatically using:\n")
        sys.stdout.write("# " + g_program_name + " " + " ".join(sys.argv[1:]) + "\n")
        sys.stdout.write("#\n"
                         "# WARNING: The following 1-2, 1-3, and 1-4 weighting parameters were ASSUMED:\n")
        sys.stdout.write("#          " + special_bonds_command + "\n")
        sys.stdout.write(
            "#          (See http://lammps.sandia.gov/doc/special_bonds.html for details)\n")
        sys.stdout.write("\n\n")
        sys.stdout.write(ffname + " {\n\n")
        
        sys.stdout.write("  # Below we will use lammps \"set\" command to assign atom charges\n"
                         "  # by atom type.  http://lammps.sandia.gov/doc/set.html\n\n")
        
        sys.stdout.write("  write_once(\"In Charges\") {\n")
        for atype in atom2mass:
            assert(atype in atom2descr)
            sys.stdout.write("    set type @atom:" + atype + " charge " + str(atom2charge[atype]) +
                             "  # \"" + atom2descr[atype] + "\"\n")
        sys.stdout.write("  } #(end of atom partial charges)\n\n\n")
        
        
        sys.stdout.write("  write_once(\"Data Masses\") {\n")
        for atype in atom2mass:
            sys.stdout.write("    @atom:" + atype + " " + str(atom2mass[atype]) + "\n")
        sys.stdout.write("  } #(end of atom masses)\n\n\n")
        
        
        sys.stdout.write("  # ---------- EQUIVALENCE CATEGORIES for bonded interaction lookup ----------\n"
                         "  #   Each type of atom has a separate ID used for looking up bond parameters\n"
                         "  #   and a separate ID for looking up 3-body angle interaction parameters\n"
                         "  #   and a separate ID for looking up 4-body dihedral interaction parameters\n"
                         "  #   and a separate ID for looking up 4-body improper interaction parameters\n"
                         #"  #   (This is because there are several different types of sp3 carbon atoms\n"
                         #"  #   which have the same torsional properties when within an alkane molecule,\n"
                         #"  #   for example.  If they share the same dihedral-ID, then this frees us\n"
                         #"  #   from being forced define separate dihedral interaction parameters\n"
                         #"  #   for all of them.)\n"
                         "  #   The complete @atom type name includes ALL of these ID numbers.  There's\n"
                         "  #   no need to force the end-user to type the complete name of each atom.\n"
                         "  #   The \"replace\" command used below informs moltemplate that the short\n"
                         "  #   @atom names we have been using abovee are equivalent to the complete\n"
                         "  #   @atom names used below:\n\n")
        
        CHANGE for atype in atom2ffid:
        CHANGE    ffid = atype + "_ffid" + atom2ffid[atype]
        CHANGE    sys.stdout.write("  replace{ @atom:" + atype +
        CHANGE                     " @atom:" + atype + "_b" + atom2ffid[atype] + "_a" + atom2ffid[atype] + "_d" + atom2ffid[atype] + "_i" + atom2ffid[atype] + " }\n")
        CHANGE sys.stdout.write("\n\n\n\n")
        
        
        sys.stdout.write("  # --------------- Non-Bonded interactions: ---------------------\n"
                         "  # " + pair_style_link + "\n"
                         "  # Syntax:\n"
                         "  # pair_coeff    AtomType1    AtomType2   pair_style_name  parameters...\n\n")
        
        sys.stdout.write("  write_once(\"In Settings\") {\n")
        for atype in atom2vdw_e:
            assert(atype in atom2vdw_s)
            CHANGE assert(atype in atom2ffid)
        
            CHANGE sys.stdout.write("    pair_coeff " +
            CHANGE                  "@atom:" + atype + "_b" + atom2ffid[atype] + "_a" + atom2ffid[
            CHANGE                      atype] + "_d" + atom2ffid[atype] + "_i" + atom2ffid[atype] + " "
            CHANGE                  "@atom:" + atype + "_b" + atom2ffid[atype] + "_a" + atom2ffid[atype] + "_d" + atom2ffid[atype] + "_i" + atom2ffid[atype] + " " +
            CHANGE                  pair_style_name +
            CHANGE                  " " + str(atom2vdw_e[atype]) +
            CHANGE                  " " + str(atom2vdw_s[atype]) + "\n")
        sys.stdout.write("  } #(end of pair_coeffs)\n\n\n\n")
        
        
        

        ################# Print 2-body Bond Interactions ##################
        bond_names_priority_high_to_low = sorted(bond2priority.items(),
                                                 key=itemgetter(1),
                                                 reverse=True)

        if len(bond_names_priority_high_to_low) > 0:
            # Print rules for generating (2-body) "bond" interactions:
            sys.stdout.write('\n\n\n'
                             '  write_once("Data Bonds By Type") {\n')
            for bond_name, in bond_names_priority_high_to_low:
                anames = ['*' if x=='X' else x
                          for x in ExtractANames(bond_name)]
                # Did the user ask us to include "auto" interactions?
                if IsAutoInteraction(bond_name):
                    if include_auto_equivalences:
                        sys.stdout.write('    @bond:' + bond_name + ' ' +
                                         ' @atom:*,aq*,ab' + anames[0] +
                                         'aae*,aac*,ade*,adc*,aie*,aic*' +
                                         ' @atom:*,aq*,ab' + anames[1] +
                                         'aae*,aac*,ade*,adc*,aie*,aic*' +
                                         '\n')
                    else:
                        continue
                else:
                    sys.stdout.write('    @bond:' + bond_name + ' ' +
                                     ' @atom:*,b' + anames[0] + ',a*,d*,i* ' +
                                     ' @atom:*,b' + anames[1] + ',a*,d*,i* ' +
                                     '\n')

            sys.stdout.write('  }  # end of "Data Bonds By Type" section\n'
                             '\n')

            # Print the force-field parameters for these bond interactions:
            sys.stdout.write('\n\n'
                             '  # ------- Bonded Interactions: -------\n'
                             '  # Syntax:  \n'
                             '  # bond_coeff BondTypeName  BondStyle  parameters...\n\n')
            sys.stdout.write('\n'
                             '  write_once("In Settings") {\n')
            for bond_name, in bond_names_priority_high_to_low:
                # Did the user ask us to include "auto" interactions?
                if (IsAutoInteraction(bond_name) and
                    (not include_auto_equivalences)):
                    continue
                sys.stdout.write('    bond_coeff @bond:'+bond_name+' '+
                                 bond2style[bond_name] +
                                 bond2params[bond_name] + '\n')
            sys.stdout.write('  }  # end of bond_coeff commands\n'
                             '\n\n')






        ################# Print 3-body Angle Interactions ##################

        angle_names_priority_high_to_low = sorted(angle2priority.items(),
                                                  key=itemgetter(1),
                                                  reverse=True)

        if len(angle_names_priority_high_to_low) > 0:
            # Print rules for generating 3-body "angle" interactions:
            sys.stdout.write('\n\n\n'
                             '  write_once("Data Angles By Type") {\n')
            for angle_name, in angle_names_priority_high_to_low:
                anames = ['*' if x=='X' else x
                          for x in ExtractANames(angle_name)]
                # Did the user ask us to include "auto" interactions?
                if IsAutoInteraction(angle_name):
                    if include_auto_equivalences:
                        sys.stdout.write('    @angle:' + angle_name + ' ' +
                                         ' @atom:*,aq*,ab*,aae' + anames[0] +
                                         ',aac*,ade*,adc*,aie*,aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac'+anames[1] +
                                         ',ade*,adc*,aie*,aic*' +
                                         ' @atom:*,aq*,ab*,aae' + anames[2] +
                                         ',aac*,ade*,adc*,aie*,aic*' +
                                         '\n')
                    else:
                        continue
                else:
                    sys.stdout.write('    @angle:' + angle_name + ' ' +
                                     ' @atom:*,b*,a' + anames[0] + ',d*,i* ' +
                                     ' @atom:*,b*,a' + anames[1] + ',d*,i* ' +
                                     '\n')

            sys.stdout.write('  }  # end of "Data Angles By Type" section\n'
                             '\n')

            # Print the force-field parameters for these angle interactions:
            sys.stdout.write('\n\n'
                             '  # ------- Angle Interactions: -------\n'
                             '  # Syntax:  \n'
                             '  # angle_coeff AngleTypeName  AngleStyle  parameters...\n\n')
            sys.stdout.write('\n'
                             '  write_once("In Settings") {\n')
            for angle_name, in angle_names_priority_high_to_low:
                # Did the user ask us to include "auto" interactions?
                if (IsAutoInteraction(angle_name) and
                    (not include_auto_equivalences)):
                    continue
                sys.stdout.write('    angle_coeff @angle:'+angle_name+' '+
                                 angle2style[angle_name] +
                                 angle2param[angle_name] + '\n')
                if angle_name in angle2class2_bb:
                    sys.stdout.write('    angle_coeff @angle:'+angle_name+' '+
                                     'bb ' + 
                                     angle2class2_bb[angle_name] + '\n')
                    assert(angle_name in angle2class2_ba)
                    sys.stdout.write('    angle_coeff @angle:'+angle_name+' '+
                                     'ba ' + 
                                     angle2class2_ba[angle_name] + '\n')
            sys.stdout.write('  }  # end of angle_coeff commands\n'
                             '\n\n')


        ################# Print 4-body Dihedral Interactions ##################

        dihedral_names_priority_high_to_low = sorted(dihedral2priority.items(),
                                                  key=itemgetter(1),
                                                  reverse=True)

        if len(dihedral_names_priority_high_to_low) > 0:
            # Print rules for generating 4-body "dihedral" interactions:
            sys.stdout.write('\n\n'
                             '  write_once("Data Dihedrals By Type") {\n')
            for dihedral_name, in dihedral_names_priority_high_to_low:
                anames = ['*' if x=='X' else x
                          for x in ExtractANames(dihedral_name)]
                # Did the user ask us to include "auto" interactions?
                if IsAutoInteraction(dihedral_name):
                    if include_auto_equivalences:
                        sys.stdout.write('    @dihedral:' + dihedral_name + ' ' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade'
                                         + anames[0] +
                                         ',adc*,aie*,aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc'
                                         + anames[1] +
                                         ',aie*,aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc'
                                         + anames[2] +
                                         ',aie*,aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade'
                                         + anames[3] +
                                         ',adc*,aie*,aic*' +
                                         '\n')
                    else:
                        continue
                else:
                    sys.stdout.write('    @dihedral:' + dihedral_name + ' ' +
                                     ' @atom:*,b*,a*,d' + anames[0] + ',i* ' +
                                     ' @atom:*,b*,a*,d' + anames[1] + ',i* ' +
                                     '\n')

            sys.stdout.write('  }  # end of "Data Dihedrals By Type" section\n'
                             '\n')

            # Print the force-field parameters for these dihedral interactions:
            sys.stdout.write('\n\n'
                             '  # ------- Dihedral Interactions: -------\n'
                             '  # Syntax:  \n'
                             '  # dihedral_coeff DihedralTypeName  DihedralStyle  parameters...\n\n')
            sys.stdout.write('\n'
                             '  write_once("In Settings") {\n')
            for dihedral_name, in dihedral_names_priority_high_to_low:
                # Did the user ask us to include "auto" interactions?
                if (IsAutoInteraction(dihedral_name) and
                    (not include_auto_equivalences)):
                    continue
                sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                 dihedral2style[dihedral_name] +
                                 dihedral2param[dihedral_name] + '\n')
                if dihedral_name in dihedral2class2_mbt:
                    sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                     'mbt ' + 
                                     dihedral2class2_mbt[dihedral_name] + '\n')
                    assert(dihedral_name in dihedral2class2_ebt)
                    sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                     'ebt ' + 
                                     dihedral2class2_ebt[dihedral_name] + '\n')
                    assert(dihedral_name in dihedral2class2_at)
                    sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                     'at ' + 
                                     dihedral2class2_at[dihedral_name] + '\n')
                    assert(dihedral_name in dihedral2class2_aat)
                    sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                     'aat ' + 
                                     dihedral2class2_aat[dihedral_name] + '\n')
                    assert(dihedral_name in dihedral2class2_bb13)
                    sys.stdout.write('    dihedral_coeff @dihedral:'+dihedral_name+' '+
                                     'bb13 ' + 
                                     dihedral2class2_bb13[dihedral_name] + '\n')
            sys.stdout.write('  }  # end of dihedral_coeff commands\n'
                             '\n\n')





        ################# Print 4-body Improper Interactions ##################

        improper_names_priority_high_to_low = sorted(improper2priority.items(),
                                                  key=itemgetter(1),
                                                  reverse=True)

        if len(improper_names_priority_high_to_low) > 0:
            # Print rules for generating 4-body "improper" interactions:
            sys.stdout.write('\n\n\n'
                             '  write_once("Data Impropers By Type") {\n')
            for improper_name, in improper_names_priority_high_to_low:
                anames = ['*' if x=='X' else x
                          for x in ExtractANames(improper_name)]
                # Did the user ask us to include "auto" interactions?
                if IsAutoInteraction(improper_name):
                    if include_auto_equivalences:
                        sys.stdout.write('    @improper:' + improper_name +' '+
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc*,aie'
                                         + anames[0] + ',aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc*,aie*,aic'
                                         + anames[1] +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc*,aie'
                                         + anames[2] + ',aic*' +
                                         ' @atom:*,aq*,ab*,aae*,aac*,ade*,adc*,aie'
                                         + anames[3] + ',aic*' +
                                         '\n')
                    else:
                        continue
                else:
                    sys.stdout.write('    @improper:' + improper_name + ' ' +
                                     ' @atom:*,b*,a*,d*,i' + anames[0] + 
                                     ' @atom:*,b*,a*,d*,i' + anames[1] +
                                     ' @atom:*,b*,a*,d*,i' + anames[2] +
                                     ' @atom:*,b*,a*,d*,i' + anames[3] +
                                     '\n')

            sys.stdout.write('  }  # end of "Data Impropers By Type" section\n'
                             '\n')

            # Print the force-field parameters for these improper interactions:
            sys.stdout.write('\n\n'
                             '  # ------- Improper Interactions: -------\n'
                             '  # Syntax:  \n'
                             '  # improper_coeff ImproperTypeName  ImproperStyle  parameters...\n\n')
            sys.stdout.write('\n'
                             '  write_once("In Settings") {\n')
            for improper_name, in improper_names_priority_high_to_low:
                # Did the user ask us to include "auto" interactions?
                if (IsAutoInteraction(improper_name) and
                    (not include_auto_equivalences)):
                    continue
                sys.stdout.write('    improper_coeff @improper:'+improper_name+' '+
                                 improper2style[improper_name] +
                                 improper2param[improper_name] + '\n')
                if improper_name in improper2class2_aa:
                    sys.stdout.write('    improper_coeff @improper:'+improper_name+' '+
                                     'aa ' + 
                                     improper2class2_aa[improper_name] + '\n')
            sys.stdout.write('  }  # end of improper_coeff commands\n'
                             '\n\n')



        sys.stdout.write('\n\n\n'
                         '  # ---------------- Select LAMMPS style(s) --------------\n'
                         '\n')

        
        sys.stdout.write('\n'
                         '  # LAMMPS supports many different kinds of bonded and non-bonded\n'
                         '  # interactions which can be selected at run time.  Eventually\n'
                         '  # we must inform LAMMPS which of them we will need.  We specify\n'
                         '  # this in the "In Init" section: \n\n')
        
        sys.stdout.write('  write_once("In Init") {\n')
        sys.stdout.write('    units real\n')
        sys.stdout.write('    atom_style full\n')

        sys.stdout.write('\n'
        sys.stdout.write('    bond_style hybrid')
        for bond_style in bond_styles:
            sys.stdout.write(' ' + bond_style)
        sys.stdout.write('\n')
        for bond_style in bond_styles:
            sys.stdout.write('    # '+bond_style2docs[bond_style]+'\n')

        sys.stdout.write('\n'
        sys.stdout.write('    angle_style hybrid')
        for angle_style in angle_styles:
            sys.stdout.write(' ' + angle_style)
        sys.stdout.write('\n')
        for angle_style in angle_styles:
            sys.stdout.write('    # '+angle_style2docs[angle_style]+'\n')
        sys.stdout.write('\n')

        sys.stdout.write('\n'
        sys.stdout.write('    dihedral_style hybrid')
        for dihedral_style in dihedral_styles:
            sys.stdout.write(' ' + dihedral_style)
        sys.stdout.write('\n')
        for dihedral_style in dihedral_styles:
            sys.stdout.write('    # '+dihedral_style2docs[dihedral_style]+'\n')
        sys.stdout.write('\n')

        sys.stdout.write('\n'
        sys.stdout.write('    improper_style hybrid')
        for improper_style in improper_styles:
            sys.stdout.write(' ' + improper_style)
        sys.stdout.write('\n')
        for improper_style in improper_styles:
            sys.stdout.write('    # '+improper_style2docs[improper_style]+'\n')
        sys.stdout.write('\n')

        sys.stdout.write('\n'
        sys.stdout.write('    pair_style hybrid')
        for pair_style in pair_styles:
            sys.stdout.write(' ' + pair_style +
                             ' ' + pair_style_args[pair_style])
        sys.stdout.write('\n')
        for pair_style in pair_styles:
            sys.stdout.write('    # '+pair_style2docs[pair_style]+'\n')

        sys.stdout.write('    pair_modify mix ' + pair_mixing_style + '\n')
        sys.stdout.write('    ' + special_bonds_command + '\n')
        sys.stdout.write(kspace_style)
        sys.stdout.write('  } #end of init parameters\n\n')
        sys.stdout.write('}  # ' + ffname + '\n\n')
        
        
        #sys.stderr.write(' done.\n')
        
        if filename_in != '':
            file_in.close()
    
    
    
        
    except Exception as err:
        sys.stderr.write('\n\n' + str(err) + '\n')
        sys.exit(1)


if __name__ == '__main__':
    main()