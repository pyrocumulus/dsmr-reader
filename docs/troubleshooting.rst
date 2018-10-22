Troubleshooting
===============
If the application happens to stall unexpectedly, you can perform some debugging on your end.

Status page
-----------
The first place to look at is the Status page in the application.
Does it display any errors or is it lagging data processing?


Logging
-------
Starting from DSMR-reader ``v1.24`` all logging output has been reduced by default.
You can, however, have the application log more verbose by increasing the logging level.

* Open the ``dsmrreader/settings.py`` file and look for the code below::

    """
        Enable and change the logging level below to alter the verbosity of the (backend) command(s).
        - DEBUG:             Log everything.
        - INFO:              Log most things.
        - WARNING (default): Log only warnings and errors.
    
        Restart the commands in Supervisor to apply any changes.
    """
    # LOGGING['loggers']['commands']['level'] = 'DEBUG'

* **If you cannot find the code above, you've probably installed DSMR-reader before v1.24 and you can paste the code yourself in the file.**
* Now remove the ``#`` and set the value to ``DEBUG``.
* Make sure that you execute ``./post-deploy.sh`` after changing the settings.
* Read below for more information about where to find the logging output.


Supervisor
----------
You can view the Supervisor logfiles, depending on whether your datalogger, webinterface or the data processing is broken.

The logfiles are located by default in:

* ``/var/log/supervisor/dsmr_backend.log`` *(Logs regarding the backend process)*
* ``/var/log/supervisor/dsmr_datalogger.log`` *(Logs regarding the datalogger)*
* ``/var/log/supervisor/dsmr_webinterface.log`` *(Logs regarding the webinterface)*


Appplication / Django
---------------------
The application has its own logfiles as well.
You can find them in the ``logs`` directory inside the project folder.

The logfiles are located by default in:

* ``/home/dsmr/dsmr-reader/logs/django.log`` *(lists any internal errors regarding the Django framework it's using)*
* ``/home/dsmr/dsmr-reader/logs/dsmrreader.log`` *(contains application logging, if enabled)*


Telegrams
---------
You can log all telegrams received, in base64 format, by adding ``DSMRREADER_LOG_TELEGRAMS = True`` to your ``dsmrreader/settings.py`` config. 
Make sure that you execute ``./post-deploy.sh`` after changing the settings. 
It should now log the telegrams into ``dsmrreader.log``.


Contact
-------
Are you unable to resolve your problem or do you need any help?
:doc:`More information can be found here<contributing>`.
