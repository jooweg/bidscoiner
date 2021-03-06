#!/usr/bin/env python
"""
Converts datasets in the rawfolder to nifti datasets in the bidsfolder according to the BIDS standard
"""

import os
import bids
import glob
import pandas as pd
import subprocess
import json


def coin_dicom(session, bidsmap, bidsfolder, personals):
    """
    Converts the session dicom-files into BIDS-valid nifti-files in the corresponding bidsfolder and
    extracts personals (e.g. Age, Sex) from the dicom header

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    global logfile

    # Get a valid BIDS subject identifier from the (first) dicom-header or from the session source folder
    if bidsmap['DICOM']['participant_label'] and bidsmap['DICOM']['participant_label'].startswith('<') and bidsmap['DICOM']['participant_label'].endswith('>'):
        subid = 'sub-' + bids.get_dicomfield(bidsmap['DICOM']['participant_label'][1:-1], bids.get_dicomfile(bids.lsdirs(session)[0]))
    else:
        subid = 'sub-' + session.rsplit('/sub-',1)[1].split('/ses-',1)[0]
    if subid == 'sub-':
        bids.printlog('Error: No valid subject identifier found for: ' + session, logfile)
        return

    # Get a BIDS session identifier from the (first) dicom-header or from the session source folder
    if bidsmap['DICOM']['session_label'] and bidsmap['DICOM']['session_label'].startswith('<') and bidsmap['DICOM']['session_label'].endswith('>'):
        sesid = 'ses-' + bids.get_dicomfield(bidsmap['DICOM']['session_label'][1:-1], bids.get_dicomfile(bids.lsdirs(session)[0]))
    elif '/ses-' in session:
        sesid = 'ses-' + session.rsplit('/ses-')[1]
    else:
        sesid = ''

    # Create the BIDS session-folder
    bidsseries = os.path.join(bidsfolder, subid, sesid)         # NB: This gives a trailing '/' if ses=='', but that should be ok
    os.makedirs(bidsseries, exist_ok=True)

    # Process all the dicom series subfolders
    for series in bids.lsdirs(session):

        bids.printlog('Processing dicom-folder: ' + series, logfile)

        # Get the cleaned-up bids labels from a dicom-file and bidsmap
        dicomfile = bids.get_dicomfile(series)
        result    = bids.get_matching_dicomseries(dicomfile, bidsmap)
        series_   = result['series']
        modality  = result['modality']

        # Create the BIDS session/modality folder
        bidsmodality = os.path.join(bidsseries, modality)
        os.makedirs(bidsmodality, exist_ok=True)

        # Compose the BIDS filename using the bids labels and run-index
        bidsname = bids.get_bidsname(subid, sesid, modality, series_, '1')
        bidsname = bids.increment_runindex(bidsmodality, bidsname)

        # Convert the dicom-files in the series folder to nifti's in the BIDS-folder
        command = 'module add dcm2niix; dcm2niix {options} -f {filename} -o {outfolder} {infolder}'.format(
            options   = bidsmap['Options']['dcm2niix'],
            filename  = bidsname,
            outfolder = bidsmodality,
            infolder  = series)
        bids.printlog('$ ' + command, logfile)
        process = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        bids.printlog(process.stdout.decode('utf-8'), logfile)
        if process.returncode != 0:
            errormsg = 'Error: Failed to process {} (errorcode {})'.format(series, process.returncode)
            bids.printlog(errormsg, logfile)
            continue

        # Add a dummy b0 bval- and bvec-file for any file without a bval/bvec file (e.g. sbref, b0 scans)
        if modality == 'dwi':
            for jsonfile in glob.glob(os.path.join(bidsmodality, bidsname + '*.json')):    # Account for files with _c%d, _e%d and _ph suffixes (see below)
                bvecfile = os.path.splitext(jsonfile)[0] + '.bvec'
                bvalfile = os.path.splitext(jsonfile)[0] + '.bval'
                if not os.path.isfile(bvecfile):
                    with open(bvecfile, 'w') as bvec_fid:
                        bids.printlog('Adding dummy bvec file: ' + bvecfile, logfile)
                        bvec_fid.write('0\n0\n0\n')
                if not os.path.isfile(bvalfile):
                    with open(bvalfile, 'w') as bval_fid:
                        bids.printlog('Adding dummy bval file: ' + bvalfile, logfile)
                        bval_fid.write('0\n')

        # Add the TaskName to the generated func json-file.
        if modality == 'func':
            for jsonfile in glob.glob(os.path.join(bidsmodality, bidsname + '*.json')):    # Account for files with _c%d, _e%d and _ph suffixes (see below)
                with open(jsonfile, 'r') as json_fid:
                    data = json.load(json_fid)
                if not 'TaskName' in data:
                    with open(jsonfile, 'w') as json_fid:
                        data['TaskName'] = bidsname.rsplit('_task-', 1)[1].split('_', 1)[0]
                        bids.printlog('Adding TaskName to: ' + jsonfile, logfile)
                        json.dump(data, json_fid, indent=4)

        # Check for files with _c%d, _e%d and _ph: These are produced by dcm2niix for multi-coil data, multi-echo data and phase data, respectively
        for suffix in ('_c', '_e', '_ph'):
            for filename in glob.glob(os.path.join(bidsmodality, bidsname) + suffix + '*'):
                basepath, ext1  = os.path.splitext(filename)
                basepath, ext2  = os.path.splitext(basepath)        # Account for .nii.gz files
                basepath, index = basepath.rsplit(suffix,1)
                if suffix=='_e' and bids.set_bidslabel(basepath, 'echo') and index:
                    basepath = bids.set_bidslabel(basepath, 'echo', index)
                else:
                    basepath = bids.set_bidslabel(basepath, 'dummy', suffix.upper() + index)                # --> append to acq-label, may need to be elaborated for future BIDS standards, supporting multi-coil data
                newbidsname = bids.increment_runindex(bidsmodality, os.path.basename(basepath), ext2+ext1)  # Update the runindex now that the acq-label has changed
                newfilename = os.path.join(bidsmodality, newbidsname + ext2+ext1)
                bids.printlog('Found dcm2niix {} suffix, renaming\n{} ->\n{}'.format(suffix, filename, newfilename), logfile)
                os.rename(filename, newfilename)

    # Collect personal data from the DICOM header
    dicomfile                   = bids.get_dicomfile(series)
    personals['participant_id'] = subid
    if sesid:
        personals['session_id'] = sesid                                                     # TODO: Check if this can be in the participants.tsv file according to BIDS
    personals['age']            = bids.get_dicomfield('PatientAge',    dicomfile)
    personals['sex']            = bids.get_dicomfield('PatientSex',    dicomfile)
    personals['size']           = bids.get_dicomfield('PatientSize',   dicomfile)
    personals['weight']         = bids.get_dicomfield('PatientWeight', dicomfile)


def coin_par(session, bidsmap, bidsfolder, personals):
    """

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    global logfile
    bids.printlog('coin_par is WIP!!!', logfile)


def coin_p7(session, bidsmap, bidsfolder, personals):
    """

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    global logfile
    bids.printlog('coin_p7 is WIP!!!', logfile)


def coin_nifti(session, bidsmap, bidsfolder, personals):
    """

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    global logfile
    bids.printlog('coin_nifti is WIP!!!', logfile)


def coin_filesystem(session, bidsmap, bidsfolder, personals):
    """

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    global logfile
    bids.printlog('coin_filesystem is WIP!!!', logfile)


def coin_plugin(session, bidsmap, bidsfolder, personals):
    """
    Run the plugin coiner to cast the series into the bids folder

    :param str session:    The full-path name of the subject/session source folder
    :param dict bidsmap:   The full mapping heuristics from the bidsmap yaml-file
    :param str bidsfolder: The full-path name of the BIDS root-folder
    :param dict personals: The dictionary with the personal information
    :return:               Nothing
    :rtype: NoneType
    """

    from importlib import import_module
    global logfile

    # Import and run the plugins
    for pluginfunction in bidsmap['PlugIn']:
        plugin = import_module(os.path.join(__file__,'..','plugins', pluginfunction))
        # TODO: check first if the plug-in function exist
        plugin.bidscoiner(session, bidsmap, bidsfolder, personals)


def bidscoiner(rawfolder, bidsfolder, subjects=[], force=False, participants=False, bidsmapfile='code/bidsmap.yaml'):
    """
    Main function that processes all the subjects and session in the rawfolder and uses the
    bidsmap.yaml file in bidsfolder/code to cast the data into the BIDS folder.

    :param str rawfolder:     The root folder-name of the sub/ses/data/file tree containing the source data files
    :param str bidsfolder:    The name of the BIDS root folder
    :param list subjects:     List of selected sub-# names / folders to be processed. Otherwise all subjects in the rawfolder will be selected
    :param bool force:        If True, subjects will be processed, regardless of existing folders in the bidsfolder. Otherwise existing folders will be skipped
    :param bool participants: If True, subjects in particpants.tsv will not be processed (this could be used e.g. to protect these subjects from being reprocessed), also when force=True
    :param str bidsmapfile:   The name of the bidsmap yaml-file. If the bidsmapfile is relative (i.e. no "/" in the name) then it is assumed to be located in bidsfolder/code/
    :return:                  Nothing
    :rtype: NoneType
    """

    # Input checking
    rawfolder  = os.path.abspath(os.path.expanduser(rawfolder))
    bidsfolder = os.path.abspath(os.path.expanduser(bidsfolder))
    os.makedirs(os.path.join(bidsfolder,'code'), exist_ok=True)
    if not os.path.isfile(os.path.join(bidsfolder,'.bidsignore')):
        with open(os.path.join(bidsfolder,'.bidsignore'), 'w') as bidsignore:
            bidsignore.write(bids.unknownmodality + '/')

    # Start logging
    global logfile
    logfile = os.path.join(bidsfolder, 'code', 'bidscoiner.log')
    bids.printlog('------------ START ------------\n>>> bidscoiner rawfolder={arg1} bidsfolder={arg2} subjects={arg3} force={arg4} participants={arg5} bidsmap={arg6}'.format(
        arg1=rawfolder, arg2=bidsfolder, arg3=subjects, arg4=force, arg5=participants, arg6=bidsmapfile), logfile)

    # Get the bidsmap heuristics from the bidsmap yaml-file
    bidsmap = bids.get_heuristics(bidsmapfile, os.path.join(bidsfolder,'code'))

    # Get the table with subjects that have been processed
    participants_file = os.path.join(bidsfolder, 'participants.tsv')
    if os.path.exists(participants_file):
        participants_table = pd.read_table(participants_file)
    else:
        participants_table = pd.DataFrame(columns = ['participant_id'])

    # Get the list of subjects
    if not subjects:
        subjects = bids.lsdirs(rawfolder, 'sub-*')
    else:
        subjects = [os.path.join(rawfolder,subject) for subject in subjects if os.path.isdir(os.path.join(rawfolder,subject))]

    # Loop over all subjects and sessions and convert them using the bidsmap entries
    for subject in subjects:

        if participants and subject in list(participants_table.participant_id): continue

        sessions = bids.lsdirs(subject, 'ses-*')
        if not sessions: sessions = subject
        for session in sessions:

            # Check if we should skip the session-folder
            personals = dict()
            if not force and os.path.isdir(session.replace(rawfolder, bidsfolder)):
                continue

            # Update / append the dicom mapping
            if bidsmap['DICOM']:
                coin_dicom(session, bidsmap, bidsfolder, personals)

            # Update / append the PAR/REC mapping
            if bidsmap['PAR']:
                coin_par(session, bidsmap, bidsfolder, personals)

            # Update / append the P7 mapping
            if bidsmap['P7']:
                coin_p7(session, bidsmap, bidsfolder, personals)

            # Update / append the nifti mapping
            if bidsmap['Nifti']:
                coin_nifti(session, bidsmap, bidsfolder, personals)

            # Update / append the file-system mapping
            if bidsmap['FileSystem']:
                coin_filesystem(session, bidsmap, bidsfolder, personals)

            # Update / append the plugin mapping
            if bidsmap['PlugIn']:
                coin_plugin(session, bidsmap, bidsfolder, personals)

        # Write the collected personals to the participants_file
        if personals:
            for key in personals:
                if key not in participants_table.columns:
                    participants_table[key] = None
            participants_table = participants_table.append(personals, ignore_index=True, verify_integrity=True)
            participants_table.to_csv(participants_file, sep='\t', encoding='utf-8', index=False)

    bids.printlog('------------ FINISHED! ------------', logfile)


# Shell usage
if __name__ == "__main__":

    # Parse the input arguments and run bidscoiner(args)
    import argparse
    import textwrap
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=textwrap.dedent(__doc__),
                                     epilog='examples:\n  bidscoiner.py /project/raw /project/bids\n  bidscoiner.py -f /project/raw /project/bids -s sub-009 sub-030\n ')
    parser.add_argument('rawfolder',           help='The source folder containing the raw data in sub-#/ses-#/series format')
    parser.add_argument('bidsfolder',          help='The destination folder with the bids data structure')
    parser.add_argument('-s','--subjects',     help='Space seperated list of selected sub-# names / folders to be processed. Otherwise all subjects in the rawfolder will be selected', nargs='*')
    parser.add_argument('-f','--force',        help='If this flag is given subjects will be processed, regardless of existing folders in the bidsfolder. Otherwise existing folders will be skipped', action='store_true')
    parser.add_argument('-p','--participants', help='If this flag is given those subjects that are in particpants.tsv will not be processed (also when the --force flag is given). Otherwise the participants.tsv table is ignored', action='store_true')
    parser.add_argument('-b','--bidsmap',      help='The bidsmap yaml-file with the study heuristics. If the bidsmapfile is relative (i.e. no "/" in the name) then it is assumed to be located in bidsfolder/code/. Default: bidsmap.yaml', default='bidsmap.yaml')
    args = parser.parse_args()

    bidscoiner(rawfolder=args.rawfolder, bidsfolder=args.bidsfolder, subjects=args.subjects, force=args.force, participants=args.participants, bidsmapfile=args.bidsmap)
