=======================
Logic behind the hotdog
=======================


What happened at the start of the server?
-----------------------------------------

The server will first read the `prev_csv` and send out the data in it to the proxy.
Then, it will find all the data files in the `watch_path` and check if the file is recorded in the `prev_csv`.
If the file is recorded, it won't do anything to it.
If not, it will process the data in the file and send the result to the proxy.


What happened when a new file is added?
---------------------------------------

When a new file is added to the `watch_path`,
the server will first check if this file is a file that follows the `patterns` and it is not in the `excluded_patterns`.
If the condition holds, this file is considered as a data file and the server will process it.
If not, the server will ignore it.
If there is no new file added, the server will do nothing.


What happend at the end of the server?
--------------------------------------

When the server is terminated by the `CTRL + C`,
it will send out send out the message to proxy.
The proxy will transfer this message to the listeners to let them know that it is the time to finalize their tasks.


How is one file processed?
--------------------------

First, the metadata like `time` and `filename` and useful scalar data specified in the `data_keys` will be parsed using the file name.

Then, the server will write a `.inp` file using the template specified by `inp_path`.
After that, the server will fork a subprocess to run topas using the INP file.
The topas will output the fitted result `.res`, fitted data arrays `.fit`, output file `.out`.

There are three cases how server responses to the topas output:

**Case 1** This is the first file that the server received.

The server will think that this is a room temperature measurement at the center of the sample.
The server will just record the topas output and room temperature in the cache.

**Case 2** This is the not the first file but it is the first time that the server saw the "position" recorded in the file name.

The server will consider it as the room temperature measurement but not necessay at the center of the sample.
The server will just record the topas output and calculate the correction parameter for the cell volume and record these data with the room temperature in the cache.

**Case 3** The server saw the "position" recorded in the file name before.

The server will think that it is a measurement not at room temperature and the temperature of this point needs to be calibrated.
The server will read the data that topas outputs and calibrate the temperature using it and former data recorded in the cache.
After it is done, the server will record the new processed data in the cache.

After the server responds to the topas output, it summary the data it processed and prepares sending them to the proxy.

Before sending them, the server will check the counter inside to know if this is the first time it sends out data to the proxy.

**Case 1**

If it is the first time, the server will first send out the message that a new experiment has started and what data is expected to be sent out.

**Case 2**

Then it will send out the message containing the data.
If it is not the first time, the server will only send out the messasge containing the data.
