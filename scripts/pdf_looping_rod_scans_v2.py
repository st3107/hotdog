from itertools import count


def Perform_Spatial_Mapping(Sample_Index, Expos_Time, x_start, x_end, x_points, y_start, y_end, y_points, num=0, extra_md={}, Dk_on_Rpt=False, Shutter_Logic=False, Shutter_Sleep=1.0, Gridx_cent=50, horz_y_index=3):
    """
    :Description:
    :Sample_Index: Sample index from bt.list() used for identifying sample and creates additional metadata keys. 
    :Exp_Time: Float for the total exposure time for data collection at each spatial position.
    :All_Positions: A list of lists where each inner list [H,V] contains the positional coordinates which will be moved to before each exposure. Here, H = Sample_Stage_Horizontal, V = Sample_Stage_Vertical.
    :Repeats:Number of repeats to perform, defaults to 0.Useful for line scans where translation stage is rastered repeatedly during cycling.
    :extra_md: Defaults to empty dict, but can be replaced with a dict to append additional metadata to each scan.  
    :Dk_on_Rpt: Dark on repeat. Defaults to False, but if set to True will collect a dark image after each repeat of All_Positions.
    :Shutter_Logic: Defaults to False, if set to True then shutter will close after data acquisition at each spatial position.
    :Shutter_Sleep: How long to wait in seconds after shutter closes before continuing the ScanPlan.
    Written by GM on 07/29/19 and verified to work with Dan Olds on 07/31/19 at 2:00 p.m.
    """

    # if glbl['frame_acq_time'] < 0.2:
    #print('ERROR: Frame acquisition time is too short. Minimum should be 0.2 sec')
    # return

    from xpdacq.beamtime import _configure_area_det
    # xpd_configuration['area_det'] = pe1c # Ensures the front area detector is used for data collection.
    # Sets the total exposure time for all exposures before start of spatial mapping.
    # glbl['auto_dark'] = True # This must be true for automated dark subtraction to be performed for raw images.
    # glbl['dk_window'] = 432000 # Dark window in *seconds*. Set to a very large value so this does not interfere with function-defined dark window.
    # time.sleep(5)            # Sleep timer, so XPDAcq waits for settings to update before executing run.
    #print ('Detector configuration updated.\n')
    t0 = time.time()

    def _elapsed_time() -> float:
        return time.time() - t0

    def _get_md(x: float, y: float) -> dict:
        md = {
            **extra_md,
            **{
                'Grid_X': x,
                'Grid_Y': y,
                'Elapsed_Time': str(_elapsed_time()),
                'frame_acq_time': glbl['frame_acq_time'],
                'exposure_time': Expos_Time,
            }
        }
        return md

    def _close_shutter():
        if Shutter_Logic == True:
            yield from bps.mv(fs, 'Close')
            yield from bps.sleep(Shutter_Sleep)
        return

    def _collect_image_at(x: float, y: float):
        yield from _close_shutter()
        yield from bps.mv(Grid_X, x, Grid_Y, y, fs, 'Open')
        xr = (yield from bps.rd(Grid_X))
        yr = (yield from bps.rd(Grid_Y))
        md = _get_md(xr, yr)
        yield from bp.list_grid_scan([pe1c], Grid_Y, [v], Grid_X, [Gridx_cent], md=md)
        return

    def _update_counter(counter: int):
        counter = counter + 1
        print('\n\n on cycle ' + str(counter))
        print('seconds passed ' + str(_elapsed_time()))
        return counter

    def _cross_scan(counter: int):
        all_h_positions = list(np.linspace(x_start, x_end, x_points))
        all_v_positions = list(np.linspace(y_start, y_end, y_points))
        print('scan start')
        for v in all_v_positions:
            print('moving vertical')
            yield from _collect_image_at(Gridx_cent, v)
        counter = _update_counter(counter)
        Gridy_cent = all_v_positions[horz_y_index]
        yield from bps.mv(Grid_Y, Gridy_cent)
        for h in all_h_positions:
            print('moving horizontal')
            yield from _collect_image_at(h, Gridy_cent)
        counter = _update_counter(counter)
        if Dk_on_Rpt == True:
            yield from take_dark()
        print('scan complete')
        return counter

    def ScanPlan_Generator():
        yield from _configure_area_det(Expos_Time)
        # Takes at least one dark, prior to the start of spatial data collection. Automatically closes shutter.
        yield from take_dark()
        counter = 0
        if num:
            for _ in range(num + 1):
                counter = (yield from _cross_scan(counter))
        else:
            while True:
                counter = (yield from _cross_scan(counter))
        return
    
    Master_Plan = ScanPlan_Generator()
    # GM: When xrun is called, fs will automatically close after the ScanPlan is executed.
    xrun(Sample_Index, Master_Plan, user_config=my_user_config)
    print('ScanPlan finished.\n')
